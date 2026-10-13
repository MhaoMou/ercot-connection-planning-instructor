# RL Gym Phase 7 Hourly Operation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a separate, deterministic hourly-operation environment around a frozen team plan without changing the existing planning environment or production evaluator.

**Architecture:** Implement `HourlyOperationEnv` in `rl_gym/hourly_env.py` as a solver-free local transition layer. It consumes a validated `RLPlanState`, exposes a fixed hourly action/observation contract, optionally calls an application-owned terminal evaluator once, and emits privacy-safe metadata. Keep evaluator/dispatch/scoring ownership in existing production modules and extend documentation/tests only for the new RL contract.

**Tech Stack:** Python 3.13, NumPy, optional Gymnasium with the project’s fallback spaces, pytest, existing `RLPlanState`, `ReleaseManifest`, and `half_year_calendar` APIs.

---

## File map and boundaries

- Create: `rl_gym/hourly_env.py` — hourly environment, strict action coercion, local storage transition, terminal callback seam, JSON-safe info.
- Create: `tests/test_rl_gym_hourly.py` — unit and contract tests for all Phase 7 acceptance criteria that do not require solver artifacts.
- Create: `tests/test_rl_gym_hourly_privacy.py` — public metadata/private-action filtering tests.
- Modify: `rl_gym/__init__.py` — export only the public hourly environment/configuration symbols.
- Modify: `rl_gym/README.md` — document the Phase 7 hourly API, explicit solver-free boundary, and usage example.
- Modify: `rl_gym/PHASE7_HOURLY_GATE.md` — link the implemented contract and mark only evidenced criteria complete.
- Create: `rl_gym/PHASE7_VERIFICATION.md` — record exact test counts, horizon counts, evaluator-call counts, timings, and parity dependency status.
- Modify: `tests/test_documentation_consistency.py` — assert the public documentation names the action, observation, reward, privacy, and evaluator boundaries.
- Do not modify: `src/ercot_game/**`, `tools/**`, network/data artifacts, projection code, dispatch code, scoring code, or existing planning environment behavior.

### Task 1: Write the failing hourly contract tests

**Files:**
- Create: `tests/test_rl_gym_hourly.py`

- [ ] **Step 1: Add construction and schema tests.**

```python
def test_hourly_env_declares_fixed_action_and_observation_contract():
    env = make_hourly_env()
    assert set(env.action_space.spaces) == {
        "generator_output_mw",
        "storage_charge_mw",
        "storage_discharge_mw",
    }
    assert set(env.observation_space.spaces) == {
        "metadata",
        "asset_state",
        "exogenous",
    }


def test_hourly_env_uses_exact_released_half_year_horizon():
    jan = make_hourly_env(block="jan_jun")
    jul = make_hourly_env(block="jul_dec")
    assert jan.horizon_hours == 4368
    assert jul.horizon_hours == 4416
```

- [ ] **Step 2: Add storage-transition tests.** Verify no-loss state updates, discharging cannot exceed state, power/energy bounds, simultaneous charge/discharge rejection, and final `SOC >= 0.5B`.
- [ ] **Step 3: Add lifecycle and determinism tests.** Verify `reset`, exactly one transition per hour, terminal flags on the final hour, stepping after termination raises, and identical seed/action traces produce identical observations/rewards/statuses.
- [ ] **Step 4: Add reward/evaluator tests.** Verify default local reward is zero and labeled, a terminal callback is called once, `terminal_score` is not double-counted, callback failure produces `evaluation_failed`, and no callback produces `not_evaluated`.
- [ ] **Step 5: Run the tests before implementation.**

Run: `/opt/anaconda3/bin/python -m pytest tests/test_rl_gym_hourly.py -q`

Expected: FAIL because `rl_gym.hourly_env` and `HourlyOperationEnv` do not yet exist.

### Task 2: Implement the minimal hourly environment

**Files:**
- Create: `rl_gym/hourly_env.py`

- [ ] **Step 1: Define immutable configuration and strict constants.** Implement `HOURLY_ENVIRONMENT_VERSION`, the three action fields, the observation groups, and constructor validation for `RLPlanState`, release identity, `invalid_action_policy`, and callback ownership.
- [ ] **Step 2: Implement plan-derived asset limits.** Derive owned generator capacity, storage power capacity, storage energy capacity, and terminal SOC requirement from the frozen plan. Do not import or reimplement evaluator dispatch logic.
- [ ] **Step 3: Implement `reset`.** Reset hour index, SOC, episode counters, terminal status, seed, and evaluator-call count. Return a JSON-safe observation and info mapping.
- [ ] **Step 4: Implement strict action coercion and validation.** Reject unknown/missing fields, non-finite values, negative values, generator over-capacity, storage power over-capacity, simultaneous charge/discharge, and energy-state violations. Use deterministic penalty-and-terminal behavior by default and strict raising when configured.
- [ ] **Step 5: Implement one-hour local transition.** Apply the no-loss storage equation, enforce `[0, B]`, advance exactly one hour, emit zero local reward, and mark the final transition with the terminal SOC check.
- [ ] **Step 6: Implement one-time terminal callback.** At termination, call only the injected application callback with the frozen plan/release context. Normalize only public status/score fields, never expose callback exceptions or raw evaluator diagnostics, and ensure later calls cannot add the score again.
- [ ] **Step 7: Make all observations/info JSON-safe.** Convert NumPy scalars to ordinary Python values and omit private action/state fields from public output by default.
- [ ] **Step 8: Run Task 1 tests.**

Run: `/opt/anaconda3/bin/python -m pytest tests/test_rl_gym_hourly.py -q`

Expected: PASS.

### Task 3: Add privacy-safe hourly artifacts and focused privacy tests

**Files:**
- Create: `tests/test_rl_gym_hourly_privacy.py`
- Modify: `rl_gym/hourly_env.py`

- [ ] **Step 1: Add tests proving public metadata excludes private action/state keys.** Test that public episode records retain release identity, hour/status/reward fields, but exclude action values, storage schedule, asset IDs, evaluator objects, solver fields, and raw diagnostics.
- [ ] **Step 2: Add explicit private opt-in export.** Implement a small `episode_record(include_private_actions=False)` method. Public output must be sanitized and marked `public_sanitized`; private output requires `include_private_actions=True` and is marked `private_record`. The hourly record is intentionally not replayable until a compatible hourly loader exists.
- [ ] **Step 3: Reject unknown/private fields in public serialization.** Reuse existing release JSON validation patterns and ensure all output is finite and JSON serializable.
- [ ] **Step 4: Run privacy tests.**

Run: `/opt/anaconda3/bin/python -m pytest tests/test_rl_gym_hourly.py tests/test_rl_gym_hourly_privacy.py -q`

Expected: PASS.

### Task 4: Export the public API and add documentation

**Files:**
- Modify: `rl_gym/__init__.py`
- Modify: `rl_gym/README.md`
- Modify: `rl_gym/PHASE7_HOURLY_GATE.md`
- Modify: `tests/test_documentation_consistency.py`

- [ ] **Step 1: Export only the intended symbols.** Add `HourlyOperationEnv` and `HOURLY_ENVIRONMENT_VERSION`; do not export private transition helpers.
- [ ] **Step 2: Document construction and a short 10-hour smoke example.** Explain that the environment wraps a frozen plan, uses one transition per released hour, has no curtailment action, defaults to zero local reward, and delegates official evaluation through an optional callback.
- [ ] **Step 3: Update the gate conservatively.** Mark only schema, privacy, deterministic local transitions, and one-time callback behavior as implemented. Keep solver-backed parity and production performance explicitly dependency-limited unless actually measured.
- [ ] **Step 4: Add documentation assertions.** Require the README/gate to contain the action fields, 4,368/4,416 horizons, zero/local reward distinction, no-curtailment boundary, privacy marker, and evaluator callback boundary.
- [ ] **Step 5: Run documentation tests.**

Run: `/opt/anaconda3/bin/python -m pytest tests/test_documentation_consistency.py -q`

Expected: PASS.

### Task 5: Produce a reproducible Phase 7 verification report

**Files:**
- Create: `rl_gym/PHASE7_VERIFICATION.md`
- Modify: `tests/test_rl_gym_hourly.py`

- [ ] **Step 1: Add a deterministic benchmark test.** Run a short fixed action trace for both released blocks, record exact transition counts and callback count, and assert no implicit horizon shortening.
- [ ] **Step 2: Record actual test evidence.** Include command, test count, runtime, environment version, release manifest behavior, and whether solver-backed parity was available.
- [ ] **Step 3: State limitations precisely.** If solver artifacts are unavailable, write `DEPENDENCY-LIMITED` rather than claiming production evaluator parity. Do not include private plans, action traces, or raw evaluator diagnostics.
- [ ] **Step 4: Run the complete focused suite.**

Run: `/opt/anaconda3/bin/python -m pytest tests/test_rl_gym*.py tests/test_documentation_consistency.py -q`

Expected: all focused RL/documentation tests pass; existing unrelated notebook failures, if any, are reported separately and not silently counted as Phase 7 passes.

### Task 6: Regression and release review

**Files:**
- No production evaluator/data files may be modified.

- [ ] **Step 1: Inspect the final diff.** Confirm only `rl_gym`, focused RL tests, documentation, and the Phase 7 plan/spec are changed.
- [ ] **Step 2: Run `git diff --check`.**
- [ ] **Step 3: Run the existing Phase 1–6 RL regression tests.**
- [ ] **Step 4: Confirm planning behavior is unchanged.** Compare the existing planning environment’s test results and public API surface before/after Phase 7.
- [ ] **Step 5: Commit the Phase 7 implementation separately.**

Commit message: `Add RL Gym Phase 7 hourly operation contract`
