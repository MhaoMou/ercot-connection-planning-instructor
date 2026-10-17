"""Two-bus hosting-capacity region and two-team example on the 6-hour package."""
import json, math, sys
from tools.activsg2000.dcopf import DCOPFInfeasible, DCOPFUnresolved, solve_prepared_period

BLOCK = "data/processed/activsg2000_scenarios_v2_6h/2028/jul_dec"
PERIOD = int(sys.argv[1]) if len(sys.argv) > 1 else 306
A, B = "1002", "1010"

def ok(la, lb):
    try:
        solve_prepared_period(BLOCK, period_index=PERIOD, additional_demand_mw_by_bus={A: la, B: lb})
        return True
    except (DCOPFInfeasible, DCOPFUnresolved):
        return False

def max_second(fixed_a, cap=400.0, tol=0.25):
    if not ok(fixed_a, 0.0):
        return None
    lo, hi = 0.0, cap
    if ok(fixed_a, hi):
        return hi
    while hi - lo > tol:
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if ok(fixed_a, mid) else (lo, mid)
    return lo

def max_first(fixed_b, cap=400.0, tol=0.25):
    lo, hi = 0.0, cap
    while hi - lo > tol:
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if ok(mid, fixed_b) else (lo, mid)
    return lo

hc_a = max_first(0.0); hc_b = max_second(0.0)
boundary = []
steps = 24
for i in range(steps + 1):
    la = hc_a * i / steps
    boundary.append((round(la, 2), max_second(la)))
def project(ra, rb):
    ca, cb = min(ra, 100), min(rb, 100)
    m = min(ca, cb); ca, cb = min(ca, m), min(cb, m)
    if ok(ca, cb):
        return ca, cb, (ca, cb) != (ra, rb)
    best = None
    for i in range(201):
        la = ca * i / 200
        lb = max_second(la, cap=cb)
        if lb is None: continue
        key = (la + lb, (math.log(la) if la > 0 else -1e9) + (math.log(lb) if lb > 0 else -1e9))
        if best is None or key[0] > best[0][0] + 1e-6 or (abs(key[0] - best[0][0]) <= 1e-6 and key[1] > best[0][1]):
            best = (key, la, lb)
    return best[1], best[2], True
V = 4368 * 6500 + 4416 * (6500 - 18)
game = {}
for ra in (50, 100):
    for rb in (50, 100):
        sa, sb, proj = project(ra, rb)
        game[f"{ra},{rb}"] = {"served": [sa, sb], "projected": proj,
                              "payoff_musd": [(V * sa - 25e6 * ra) / 1e6, (V * sb - 25e6 * rb) / 1e6]}
out = {"period": PERIOD, "buses": [A, B], "hc_alone": [hc_a, hc_b],
       "hc_a_given_b60": max_first(60.0), "boundary": boundary, "game": game}
json.dump(out, open(f"outputs/pair_region_p{PERIOD}.json", "w"), indent=1)
print(json.dumps({k: v for k, v in out.items() if k != "boundary"}, indent=1))
