"""Privacy-safe interactive grid map for classroom result releases.

The map draws the ACTIVSg2000 network as a schematic graph.  Each bus is
colored by the total connection capacity that *all* valid teams placed there,
so no individual team's plan is shown.  When dispatch results are supplied,
each branch is colored by its peak loading (largest ``|flow| / limit`` over the
evaluated hours).  Tooltips (``<title>`` elements) and a small pan/zoom script
make the SVG interactive in any browser.

The scenario package has no bus coordinates, so the layout is a deterministic
force-directed embedding of the network graph.  It is computed once and stored
as JSON so maps from different rounds share the same picture.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from html import escape
from pathlib import Path

from .csv_submission import load_bus_ids
from .csv_validator import validate_csv_submission

LAYOUT_VERSION = "activsg2000-schematic-layout-v1"
NEAR_LIMIT_FRACTION = 0.99


@dataclass(frozen=True)
class MapBranch:
    branch_id: str
    from_bus_id: str
    to_bus_id: str
    limit_mw: float


def load_network(artifact_dir: str | Path) -> tuple[tuple[str, ...], tuple[MapBranch, ...], dict[str, float]]:
    """Return bus IDs, in-service branches, and base load by bus for one block."""
    root = Path(artifact_dir)
    with (root / "bus.csv").open(newline="", encoding="utf-8") as handle:
        bus_ids = tuple(row["bus_id"] for row in csv.DictReader(handle))
    branches = []
    with (root / "branch_limits.csv").open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row.get("status", "1") != "1":
                continue
            branches.append(
                MapBranch(row["branch_id"], row["from_bus_id"], row["to_bus_id"], float(row["thermal_limit_mva"]))
            )
    base_load: dict[str, float] = {}
    load_path = root / "bus_load_base.csv"
    if load_path.is_file():
        with load_path.open(newline="", encoding="utf-8") as handle:
            base_load = {row["bus_id"]: float(row["base_load_mw"]) for row in csv.DictReader(handle)}
    return bus_ids, tuple(branches), base_load


def compute_layout(
    bus_ids: Sequence[str],
    branches: Sequence[MapBranch],
    *,
    iterations: int = 300,
    seed: int = 0,
) -> dict[str, tuple[float, float]]:
    """Deterministic force-directed layout scaled to the unit square.

    A spectral embedding of the graph Laplacian gives the starting positions;
    Fruchterman-Reingold iterations then spread the nodes out.
    """
    import numpy as np

    n = len(bus_ids)
    if n == 0:
        return {}
    index = {bus_id: i for i, bus_id in enumerate(bus_ids)}
    edges = np.array(
        [(index[b.from_bus_id], index[b.to_bus_id]) for b in branches
         if b.from_bus_id in index and b.to_bus_id in index and b.from_bus_id != b.to_bus_id],
        dtype=int,
    ).reshape(-1, 2)
    adjacency = np.zeros((n, n))
    if len(edges):
        adjacency[edges[:, 0], edges[:, 1]] = 1.0
        adjacency[edges[:, 1], edges[:, 0]] = 1.0
    laplacian = np.diag(adjacency.sum(axis=1)) - adjacency
    rng = np.random.default_rng(seed)
    if n > 3:
        _, vectors = np.linalg.eigh(laplacian + 1e-9 * np.eye(n))
        position = vectors[:, 1:3].copy()
    else:
        position = rng.random((n, 2))
    position += 1e-3 * rng.standard_normal((n, 2))
    position -= position.mean(axis=0)
    position /= np.abs(position).max() or 1.0

    k = np.sqrt(4.0 / n)
    temperature = 0.1
    cooling = temperature / (iterations + 1)
    for _ in range(iterations):
        delta = position[:, None, :] - position[None, :, :]
        distance = np.sqrt((delta ** 2).sum(axis=-1))
        np.fill_diagonal(distance, 1.0)
        distance = np.maximum(distance, 1e-3)
        force = k * k / distance ** 2 - adjacency * distance / k
        np.fill_diagonal(force, 0.0)
        displacement = (delta * force[:, :, None]).sum(axis=1)
        length = np.maximum(np.sqrt((displacement ** 2).sum(axis=1)), 1e-9)
        position += displacement / length[:, None] * np.minimum(length, temperature)[:, None]
        temperature -= cooling

    position -= position.min(axis=0)
    position /= position.max(axis=0).max() or 1.0
    return {bus_id: (round(float(position[i, 0]), 5), round(float(position[i, 1]), 5)) for bus_id, i in index.items()}


def load_or_create_layout(
    layout_path: str | Path,
    bus_ids: Sequence[str],
    branches: Sequence[MapBranch],
) -> dict[str, tuple[float, float]]:
    """Read a stored layout, or compute and store one when the file is absent."""
    path = Path(layout_path)
    if path.is_file():
        payload = json.loads(path.read_text(encoding="utf-8"))
        positions = {bus_id: (float(x), float(y)) for bus_id, (x, y) in payload["positions"].items()}
        missing = set(bus_ids) - set(positions)
        if missing:
            raise ValueError(f"layout {path} is missing {len(missing)} buses")
        return positions
    positions = compute_layout(bus_ids, branches)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"layout_version": LAYOUT_VERSION, "positions": positions}, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return positions


def aggregate_connection_by_bus(
    submission_dirs: Iterable[str | Path],
    bus_ids: Iterable[str],
) -> tuple[dict[str, float], int]:
    """Sum connection capacity per bus over every valid CSV in the directories.

    Invalid files are skipped, matching the evaluator.  Only per-bus totals are
    returned; team and site identifiers are discarded.
    """
    known = frozenset(bus_ids)
    totals: dict[str, float] = {}
    valid_files = 0
    for directory in submission_dirs:
        for path in sorted(Path(directory).glob("*.csv")):
            if not validate_csv_submission(path, bus_ids=known).valid:
                continue
            valid_files += 1
            with path.open(newline="", encoding="utf-8-sig") as handle:
                for row in csv.DictReader(handle):
                    capacity = float(row["connection_capacity_mw"])
                    if capacity > 0:
                        totals[row["bus_id"]] = totals.get(row["bus_id"], 0.0) + capacity
    return totals, valid_files


def branch_loading(
    dispatch_dir: str | Path,
    branches: Sequence[MapBranch],
) -> tuple[dict[str, tuple[float, int]], int]:
    """Peak loading and hours near the limit for each branch.

    Reads every ``period_*.json`` in ``dispatch_dir`` (the evaluator's dispatch
    output).  Returns ``{branch_id: (peak |flow|/limit, hours >= 99%)}`` and the
    number of hours read.
    """
    limits = {branch.branch_id: branch.limit_mw for branch in branches if branch.limit_mw > 0}
    peak = dict.fromkeys(limits, 0.0)
    near = dict.fromkeys(limits, 0)
    hours = 0
    for path in sorted(Path(dispatch_dir).glob("period_*.json")):
        flows = json.loads(path.read_text(encoding="utf-8")).get("flow_mw_by_branch", {})
        hours += 1
        for branch_id, limit in limits.items():
            ratio = abs(float(flows.get(branch_id, 0.0))) / limit
            if ratio > peak[branch_id]:
                peak[branch_id] = ratio
            if ratio >= NEAR_LIMIT_FRACTION:
                near[branch_id] += 1
    return {branch_id: (peak[branch_id], near[branch_id]) for branch_id in limits}, hours


def _mix(low: tuple[int, int, int], high: tuple[int, int, int], t: float) -> str:
    t = min(max(t, 0.0), 1.0)
    return "#%02x%02x%02x" % tuple(round(a + (b - a) * t) for a, b in zip(low, high))


def _bus_color(mw: float, max_mw: float) -> str:
    return _mix((254, 224, 139), (165, 0, 38), (mw / max_mw) ** 0.5 if max_mw > 0 else 0.0)


def _line_style(loading: float | None) -> tuple[str, float]:
    if loading is None:
        return "#c9ced6", 0.6
    if loading >= NEAR_LIMIT_FRACTION:
        return "#d7191c", 2.4
    if loading >= 0.8:
        return "#fd8d3c", 1.8
    if loading >= 0.5:
        return "#fdcc5c", 1.2
    return "#c9ced6", 0.6


_PAN_ZOOM_SCRIPT = """
(function () {
  var svg = document.currentScript.ownerSVGElement || document.documentElement;
  var view = svg.getElementById('view');
  var scale = 1, x = 0, y = 0, drag = null;
  function apply() { view.setAttribute('transform', 'translate(' + x + ',' + y + ') scale(' + scale + ')'); }
  svg.addEventListener('wheel', function (e) {
    e.preventDefault();
    var r = svg.getBoundingClientRect();
    var px = (e.clientX - r.left) * svg.viewBox.baseVal.width / r.width;
    var py = (e.clientY - r.top) * svg.viewBox.baseVal.height / r.height;
    var f = e.deltaY < 0 ? 1.2 : 1 / 1.2;
    x = px - (px - x) * f; y = py - (py - y) * f; scale *= f; apply();
  }, {passive: false});
  svg.addEventListener('mousedown', function (e) { drag = [e.clientX, e.clientY, x, y]; });
  window.addEventListener('mouseup', function () { drag = null; });
  window.addEventListener('mousemove', function (e) {
    if (!drag) return;
    var r = svg.getBoundingClientRect(), k = svg.viewBox.baseVal.width / r.width;
    x = drag[2] + (e.clientX - drag[0]) * k; y = drag[3] + (e.clientY - drag[1]) * k; apply();
  });
  svg.addEventListener('dblclick', function () { scale = 1; x = 0; y = 0; apply(); });
})();
"""


def render_svg(
    *,
    positions: Mapping[str, tuple[float, float]],
    branches: Sequence[MapBranch],
    connection_by_bus: Mapping[str, float],
    base_load_by_bus: Mapping[str, float] | None = None,
    loading_by_branch: Mapping[str, tuple[float, int]] | None = None,
    hours: int = 0,
    title: str = "Grid map",
    subtitle: str = "",
) -> str:
    """Return a self-contained interactive SVG document."""
    width, margin, panel = 1200, 40, 170
    plot = width - 2 * margin
    y_extent = max((y for _, y in positions.values()), default=1.0) or 1.0
    height = round(panel + y_extent * plot + margin)
    base_load_by_bus = base_load_by_bus or {}

    def point(bus_id: str) -> tuple[float, float]:
        x, y = positions[bus_id]
        return margin + x * plot, panel + y * plot

    max_mw = max(connection_by_bus.values(), default=0.0)
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" font-family="Helvetica, Arial, sans-serif">',
        "<style>.bus:hover,.site:hover{stroke:#111;stroke-width:2}"
        ".line:hover{stroke:#0969da;stroke-width:4}text{fill:#24292f}</style>",
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        f'<text x="{margin}" y="38" font-size="24" font-weight="bold">{escape(title)}</text>',
    ]
    if subtitle:
        out.append(f'<text x="{margin}" y="64" font-size="14">{escape(subtitle)}</text>')
    out.append(
        f'<text x="{margin}" y="86" font-size="12" fill="#57606a">Schematic layout, not geographic. '
        "Bus color = total connection capacity of all teams at that bus. "
        "Hover for details; scroll to zoom, drag to pan, double-click to reset.</text>"
    )
    # Legend: bus colors and line loading.
    legend_y = 112
    out.append(f'<text x="{margin}" y="{legend_y}" font-size="12" font-weight="bold">Teams\' connection at bus</text>')
    for i, fraction in enumerate((0.0, 0.25, 0.5, 0.75, 1.0)):
        cx = margin + 10 + i * 46
        out.append(f'<circle cx="{cx}" cy="{legend_y + 22}" r="7" fill="{_bus_color(fraction * max_mw, max_mw)}"/>')
        out.append(f'<text x="{cx}" y="{legend_y + 46}" font-size="11" text-anchor="middle">{fraction * max_mw:.0f}</text>')
    out.append(f'<text x="{margin + 240}" y="{legend_y + 46}" font-size="11">MW</text>')
    if loading_by_branch is not None:
        lx = margin + 330
        out.append(f'<text x="{lx}" y="{legend_y}" font-size="12" font-weight="bold">Peak line loading over {hours} h</text>')
        for i, (label, value) in enumerate((("< 50%", 0.0), ("50-80%", 0.6), ("80-99%", 0.9), ("at limit", 1.0))):
            color, stroke = _line_style(value)
            x0 = lx + i * 95
            out.append(f'<line x1="{x0}" y1="{legend_y + 22}" x2="{x0 + 40}" y2="{legend_y + 22}" stroke="{color}" stroke-width="{max(stroke, 1.5)}"/>')
            out.append(f'<text x="{x0}" y="{legend_y + 46}" font-size="11">{escape(label)}</text>')

    out.append('<g id="view">')
    ordered = sorted(
        branches,
        key=lambda b: (loading_by_branch or {}).get(b.branch_id, (0.0, 0))[0],
    )
    for branch in ordered:
        if branch.from_bus_id not in positions or branch.to_bus_id not in positions:
            continue
        loading = None
        tip = f"Line {branch.branch_id}: bus {branch.from_bus_id} to {branch.to_bus_id}, limit {branch.limit_mw:g} MW"
        if loading_by_branch is not None and branch.branch_id in loading_by_branch:
            loading, near_hours = loading_by_branch[branch.branch_id]
            tip += f"\npeak loading {loading:.0%}; at or near limit in {near_hours} of {hours} h"
        color, stroke = _line_style(loading)
        x1, y1 = point(branch.from_bus_id)
        x2, y2 = point(branch.to_bus_id)
        out.append(
            f'<line class="line" x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="{color}" stroke-width="{stroke}"><title>{escape(tip)}</title></line>'
        )
    for bus_id in sorted(positions, key=lambda b: connection_by_bus.get(b, 0.0)):
        x, y = point(bus_id)
        mw = connection_by_bus.get(bus_id, 0.0)
        tip = f"Bus {bus_id}\nbase load {base_load_by_bus.get(bus_id, 0.0):g} MW"
        if mw > 0:
            tip += f"\nteams' connection capacity (all teams): {mw:g} MW"
            radius = 3.5 + 6.5 * (mw / max_mw) ** 0.5
            out.append(
                f'<circle class="site" cx="{x:.1f}" cy="{y:.1f}" r="{radius:.1f}" fill="{_bus_color(mw, max_mw)}" '
                f'stroke="#5c0016" stroke-width="0.8"><title>{escape(tip)}</title></circle>'
            )
        else:
            out.append(
                f'<circle class="bus" cx="{x:.1f}" cy="{y:.1f}" r="1.6" fill="#8c959f">'
                f"<title>{escape(tip)}</title></circle>"
            )
    out.append("</g>")
    out.append(f"<script><![CDATA[{_PAN_ZOOM_SCRIPT}]]></script>")
    out.append("</svg>")
    return "\n".join(out) + "\n"


def build_map(
    *,
    artifact_dir: str | Path,
    submission_dirs: Sequence[str | Path],
    layout_path: str | Path,
    dispatch_dir: str | Path | None = None,
    title: str = "Grid map",
    subtitle: str = "",
) -> str:
    bus_ids, branches, base_load = load_network(artifact_dir)
    positions = load_or_create_layout(layout_path, bus_ids, branches)
    connection, _ = aggregate_connection_by_bus(submission_dirs, load_bus_ids(artifact_dir))
    loading, hours = (None, 0)
    if dispatch_dir is not None:
        loading, hours = branch_loading(dispatch_dir, branches)
    return render_svg(
        positions=positions,
        branches=branches,
        connection_by_bus=connection,
        base_load_by_bus=base_load,
        loading_by_branch=loading,
        hours=hours,
        title=title,
        subtitle=subtitle,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render the privacy-safe interactive classroom grid map (SVG)")
    parser.add_argument("--artifact-dir", required=True, type=Path, help="scenario block directory")
    parser.add_argument(
        "--submissions-dir", required=True, type=Path, action="append",
        help="directory of team CSVs; repeat to include Submission 1 sites carried into Submission 2",
    )
    parser.add_argument("--dispatch-dir", type=Path, help="evaluator dispatch/ directory with period_*.json files")
    parser.add_argument(
        "--layout", type=Path, default=Path("data/teaching/activsg2000_layout_v1.json"),
        help="stored bus layout (created if missing)",
    )
    parser.add_argument("--title", default="Grid map")
    parser.add_argument("--subtitle", default="")
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    svg = build_map(
        artifact_dir=args.artifact_dir,
        submission_dirs=args.submissions_dir,
        layout_path=args.layout,
        dispatch_dir=args.dispatch_dir,
        title=args.title,
        subtitle=args.subtitle,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(svg, encoding="utf-8")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
