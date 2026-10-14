import json
import xml.dom.minidom

from ercot_game.grid_map import (
    MapBranch,
    aggregate_connection_by_bus,
    branch_loading,
    compute_layout,
    main,
    render_svg,
)

HEADER = "team_id,site_id,bus_id,connection_capacity_mw,generator_capacity_mw,storage_power_mw,storage_energy_mwh\n"


def _block(tmp_path):
    block = tmp_path / "block"
    block.mkdir()
    (block / "bus.csv").write_text("bus_id,type\n1,3\n2,1\n3,1\n", encoding="utf-8")
    (block / "bus_load_base.csv").write_text("bus_id,base_load_mw\n1,10\n2,20\n3,30\n", encoding="utf-8")
    (block / "branch_limits.csv").write_text(
        "branch_id,from_bus_id,to_bus_id,reactance_pu,thermal_limit_mva,status\n"
        "b1,1,2,0.1,100,1\nb2,2,3,0.1,50,1\nb3,1,3,0.1,80,0\n",
        encoding="utf-8",
    )
    return block


def _submissions(tmp_path):
    folder = tmp_path / "subs"
    folder.mkdir()
    (folder / "team-alpha.csv").write_text(HEADER + "team-alpha,site-x,2,40,0,0,0\nteam-alpha,site-y,3,10,0,0,0\n", encoding="utf-8")
    (folder / "team-beta.csv").write_text(HEADER + "team-beta,site-z,2,25,10,5,20\n", encoding="utf-8")
    (folder / "team-bad.csv").write_text(HEADER + "team-bad,site-q,2,-5,0,0,0\n", encoding="utf-8")
    return folder


def test_connection_is_aggregated_per_bus_and_invalid_files_are_skipped(tmp_path):
    totals, valid = aggregate_connection_by_bus([_submissions(tmp_path)], {"1", "2", "3"})
    assert valid == 2
    assert totals == {"2": 65.0, "3": 10.0}


def test_branch_loading_reports_peak_ratio_and_hours_at_limit(tmp_path):
    dispatch = tmp_path / "dispatch"
    dispatch.mkdir()
    for hour, flows in enumerate(({"b1": 30.0, "b2": -50.0}, {"b1": -80.0, "b2": 10.0})):
        (dispatch / f"period_{hour}.json").write_text(json.dumps({"flow_mw_by_branch": flows}), encoding="utf-8")
    branches = (MapBranch("b1", "1", "2", 100.0), MapBranch("b2", "2", "3", 50.0))
    loading, hours = branch_loading(dispatch, branches)
    assert hours == 2
    assert loading["b1"] == (0.8, 0)
    assert loading["b2"] == (1.0, 1)


def test_layout_is_deterministic_and_normalized():
    buses = ("1", "2", "3", "4", "5")
    branches = tuple(MapBranch(f"b{i}", a, b, 1.0) for i, (a, b) in enumerate((("1", "2"), ("2", "3"), ("3", "4"), ("4", "5"))))
    first = compute_layout(buses, branches, iterations=50)
    assert first == compute_layout(buses, branches, iterations=50)
    assert all(0.0 <= value <= 1.0 for point in first.values() for value in point)


def test_svg_is_valid_interactive_and_hides_team_identity(tmp_path):
    block = _block(tmp_path)
    output = tmp_path / "map.svg"
    layout = tmp_path / "layout.json"
    assert main([
        "--artifact-dir", str(block),
        "--submissions-dir", str(_submissions(tmp_path)),
        "--layout", str(layout),
        "--title", "Week 2 Submission 1",
        "--output", str(output),
    ]) == 0
    svg = output.read_text(encoding="utf-8")
    xml.dom.minidom.parseString(svg)
    assert "team-alpha" not in svg and "team-beta" not in svg and "site-" not in svg
    assert "all teams): 65 MW" in svg
    assert "<title>" in svg and "<script>" in svg
    assert "b3" not in svg  # out-of-service branch is not drawn
    assert json.loads(layout.read_text(encoding="utf-8"))["positions"].keys() == {"1", "2", "3"}


def test_render_without_dispatch_draws_neutral_lines():
    svg = render_svg(
        positions={"1": (0.0, 0.0), "2": (1.0, 0.5)},
        branches=(MapBranch("b1", "1", "2", 10.0),),
        connection_by_bus={},
    )
    assert "Peak line loading" not in svg
    assert "#c9ced6" in svg
