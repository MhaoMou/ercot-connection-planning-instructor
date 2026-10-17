"""Turn the raw Electrify results in data/raw into plot tables and numbers.tex.

Run from docs/guest_lecture:  python scripts/make_lecture_data.py
All inputs come from the 2028 July-December six-hour block
(activsg2000_scenarios_v2_6h); see data/README.md for the commands.
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import linprog

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data"
PEAK_DAY = (304, 305, 306, 307)
DT = 6.0  # hours per game period
STORAGE_BUS = 7419
STORAGE_P = 50.0  # MW; B = 4P as in the game
SERIES_BUSES = (1002, 7419)

numbers: dict[str, str] = {}


def fmt(value: float, digits: int = 0) -> str:
    return f"{value:,.{digits}f}"


# --- All buses, peak day -------------------------------------------------
peak = pd.read_csv(RAW / "hc_allbuses_peakday" / "hosting_capacity_long.csv.gz")
assert (peak.status == "optimal").all()
per_bus = peak.pivot(index="bus_id", columns="period_index", values="hosting_capacity_mw")
day_min = per_bus.min(axis=1)
meta = json.loads((RAW / "hc_allbuses_peakday" / "hosting_capacity_metadata.json").read_text())
numbers["HCbuses"] = fmt(len(day_min))
numbers["HCmedian"] = fmt(day_min.median())
numbers["HCpten"] = fmt(day_min.quantile(0.10))
numbers["HCmin"] = fmt(day_min.min())
numbers["HCpninety"] = fmt(day_min.quantile(0.90))
numbers["HCpoints"] = fmt(len(peak))
wall = meta.get("wall_time_s") or meta.get("execution", {}).get("wall_time_s")
numbers["HCruntime"] = f"{len(peak):,} LPs in {wall:.0f}\\,s" if wall else f"{len(peak):,} LPs"
clipped = day_min.clip(upper=2000)
bins = np.arange(0, 2001, 100)
counts, edges = np.histogram(clipped, bins=bins)
pd.DataFrame({"bin_mw": edges[:-1] + 50, "count": counts}).to_csv(OUT / "hc_histogram.csv", index=False)
numbers["HCabove"] = fmt((day_min >= 2000).sum())

# --- Selected buses over the half-year -------------------------------------
series_frames = [
    pd.read_csv(RAW / name / "hosting_capacity_long.csv.gz")
    for name in ("hc_selected_allperiods", "hc_variable_allperiods")
    if (RAW / name).exists()
]
series = pd.concat(series_frames)
assert (series.status == "optimal").all()
wide = series.pivot(index="period_index", columns="bus_id", values="hosting_capacity_mw").sort_index()
table = pd.DataFrame({"period": wide.index})
for bus in SERIES_BUSES:
    table[f"b{bus}"] = wide[bus].values
table.to_csv(OUT / "hc_series.csv", index=False)
for bus, key in zip(SERIES_BUSES, ("One", "Two")):
    numbers[f"Ser{key}Bus"] = str(bus)
    numbers[f"Ser{key}Min"] = fmt(wide[bus].min())
    numbers[f"Ser{key}Med"] = fmt(wide[bus].median())

# --- Flexible connection: order statistic ---------------------------------
order_bus = STORAGE_BUS  # same bus as the storage example
sorted_hc = np.sort(wide[order_bus].values)
ks = np.arange(0, 61)
pd.DataFrame({"K": ks, "hc": sorted_hc[ks]}).to_csv(OUT / "order_statistic.csv", index=False)
numbers["OrdBus"] = str(order_bus)
numbers["OrdZero"] = fmt(sorted_hc[0])
numbers["OrdTen"] = fmt(sorted_hc[10])
numbers["OrdTenGain"] = fmt(100 * (sorted_hc[10] / sorted_hc[0] - 1))
numbers["OrdForty"] = fmt(sorted_hc[40])
numbers["OrdFortyGain"] = fmt(100 * (sorted_hc[40] / sorted_hc[0] - 1))


# --- Storage on the peak day -----------------------------------------------
def hc_with_storage(hc: np.ndarray, power: float, energy: float, dt: float = DT) -> float:
    """max L s.t. L - p_t <= hc_t, |p_t| <= P, daily cycle e0 = eT = B/2, 0 <= e <= B, p_t <= L."""
    n = len(hc)
    # variables: L, p_1..p_n ; maximize L -> minimize -L
    c = np.zeros(n + 1)
    c[0] = -1.0
    a_ub, b_ub = [], []
    for t in range(n):
        row = np.zeros(n + 1); row[0] = 1.0; row[1 + t] = -1.0
        a_ub.append(row); b_ub.append(hc[t])           # L - p_t <= hc_t
        row = np.zeros(n + 1); row[0] = -1.0; row[1 + t] = 1.0
        a_ub.append(row); b_ub.append(0.0)              # p_t <= L (serves own load only)
        row = np.zeros(n + 1); row[1:2 + t] = dt
        a_ub.append(row); b_ub.append(energy / 2)       # e_t = B/2 - dt*sum p >= 0
        a_ub.append(-row); b_ub.append(energy / 2)      # e_t <= B
    a_eq = [np.concatenate([[0.0], np.ones(n)])]        # e_T = e_0
    bounds = [(0, None)] + [(-power, power)] * n
    res = linprog(c, A_ub=np.array(a_ub), b_ub=b_ub, A_eq=np.array(a_eq), b_eq=[0.0], bounds=bounds, method="highs")
    assert res.status == 0, res.message
    return float(res.x[0])


day = per_bus.loc[STORAGE_BUS, list(PEAK_DAY)].values
with_storage = hc_with_storage(day, STORAGE_P, 4 * STORAGE_P)
big = hc_with_storage(day, 1e4, 4e4)
pd.DataFrame({"t": [1, 2, 3, 4], "hc": day}).to_csv(OUT / "storage_day.csv", index=False)
numbers.update({
    "StoBus": str(STORAGE_BUS), "StoP": fmt(STORAGE_P), "StoB": fmt(4 * STORAGE_P),
    "StoMin": fmt(day.min()), "StoMean": fmt(day.mean()), "StoWith": fmt(with_storage),
    "StoBig": fmt(big),
})
curve = [(p, hc_with_storage(day, p, 4 * p)) for p in np.arange(0, 301, 10)]
pd.DataFrame(curve, columns=["P", "hc"]).to_csv(OUT / "storage_curve.csv", index=False)
# Over the whole half-year at this bus: the limiting day decides.
if STORAGE_BUS in wide:
    days = wide[STORAGE_BUS].values.reshape(-1, 4)
    numbers["StoHalfNo"] = fmt(days.min())
    numbers["StoHalfWith"] = fmt(min(hc_with_storage(d, STORAGE_P, 4 * STORAGE_P) for d in days))

# --- Two-bus region and two-team example ----------------------------------
pair_file = RAW / "pair_region_p306.json"
if pair_file.exists():
    pair = json.loads(pair_file.read_text())
    pd.DataFrame(pair["boundary"], columns=["la", "lb"]).to_csv(OUT / "pair_boundary.csv", index=False)
    numbers["PairHCa"] = fmt(pair["hc_alone"][0])
    numbers["PairHCb"] = fmt(pair["hc_alone"][1])
    numbers["PairHCaGiven"] = fmt(pair["hc_a_given_b60"])
    game = pair["game"]
    cell = lambda key: f"{game[key]['payoff_musd'][0]:,.0f} / {game[key]['payoff_musd'][1]:,.0f}"
    numbers["GameSS"], numbers["GameSL"] = cell("50,50"), cell("50,100")
    numbers["GameLS"], numbers["GameLL"] = cell("100,50"), cell("100,100")
    served = game["100,100"]["served"]
    numbers["GameLLserved"] = f"A {served[0]:.0f}\\,MW, B {served[1]:.1f}\\,MW"

    def best(i: int, other: int) -> int:
        payoff = lambda mine: game[f"{mine},{other}" if i == 0 else f"{other},{mine}"]["payoff_musd"][i]
        return max((50, 100), key=payoff)

    nash = [
        (a, b) for a in (50, 100) for b in (50, 100)
        if best(0, b) == a and best(1, a) == b
    ]
    label = {50: "small", 100: "large"}
    numbers["GameNashText"] = (
        "Nash equilibrium: " + "; ".join(f"(A {label[a]}, B {label[b]})" for a, b in nash) + "."
        if nash else "No pure-strategy Nash equilibrium."
    )

# --- Eight-team six-hour run -----------------------------------------------
eight = RAW / "eight_team_6h_summary.json"
if eight.exists():
    summary = json.loads(eight.read_text())
    numbers["EightRuntime"] = fmt(summary["runtime_seconds"])
    numbers["EightMinScore"] = fmt(min(t["score"] for t in summary["teams"]) / 1e6)
    numbers["EightMaxScore"] = fmt(max(t["score"] for t in summary["teams"]) / 1e6)

lines = ["% Generated by scripts/make_lecture_data.py; do not edit by hand."]
lines += [f"\\newcommand{{\\{key}}}{{{value}}}" for key, value in sorted(numbers.items())]
(OUT / "numbers.tex").write_text("\n".join(lines) + "\n", encoding="utf-8")
print("\n".join(lines))
