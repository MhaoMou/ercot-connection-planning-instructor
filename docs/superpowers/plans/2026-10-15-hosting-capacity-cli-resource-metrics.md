# Hosting-Capacity CLI Resource Metrics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add auditable `peak_rss_mb` and `rss_delta_mb` fields to normal and interrupted hosting-capacity CLI summaries while preserving existing fields and exit behavior.

**Architecture:** Keep resource instrumentation local to `hosting_capacity_cli.py` with benchmark-compatible snapshot, conversion, and aggregation helpers. Snapshot immediately before and after `compute_hosting_capacity_matrix`; pass the derived values into both JSON summary paths. Update focused CLI tests and pilot documentation without changing the independent `/usr/bin/time -l` commands.

**Tech Stack:** Python standard-library `resource`, `pytest`, JSON CLI summaries, Markdown documentation.

---

### Task 1: Add failing resource-metric helper and summary tests

**Files:**
- Modify: `tests/test_hosting_capacity_cli.py`

- [ ] **Step 1: Add helper tests for available and unavailable readings**

Add tests beside the existing benchmark resource tests:

```python
def test_cli_resource_metrics_match_benchmark_units_and_max_delta(monkeypatch):
    from ercot_game import hosting_capacity_cli

    class FakeResource:
        RUSAGE_SELF = object()
        RUSAGE_CHILDREN = object()

        def __init__(self):
            self.values = iter((1000, 500, 1400, 2500))

        def getrusage(self, _kind):
            return SimpleNamespace(ru_maxrss=next(self.values))

    monkeypatch.setattr(hosting_capacity_cli, "resource", FakeResource())
    before = hosting_capacity_cli._resource_usage_snapshot()
    after = hosting_capacity_cli._resource_usage_snapshot()

    divisor = 1024 * 1024 if hosting_capacity_cli.sys.platform == "darwin" else 1024
    assert hosting_capacity_cli._peak_rss_mb(after) == round(2500 / divisor, 3)
    assert hosting_capacity_cli._rss_delta_mb(before, after) == round(2000 / divisor, 3)


def test_cli_resource_metrics_are_null_when_unavailable(monkeypatch):
    from ercot_game import hosting_capacity_cli

    monkeypatch.setattr(hosting_capacity_cli, "resource", None)
    before = hosting_capacity_cli._resource_usage_snapshot()
    after = hosting_capacity_cli._resource_usage_snapshot()

    assert hosting_capacity_cli._peak_rss_mb(after) is None
    assert hosting_capacity_cli._rss_delta_mb(before, after) is None
```

- [ ] **Step 2: Extend the normal and interrupted summary tests**

In the existing normal-summary test, monkeypatch the CLI snapshot helper to return `((1000, 500), (1400, 2500))` across the compute call and assert:

```python
assert summary["peak_rss_mb"] == round(2500 / divisor, 3)
assert summary["rss_delta_mb"] == round(2000 / divisor, 3)
```

In the existing interrupted-summary test, use the same snapshot sequence and assert the same two fields are present with those values. This proves both output paths use compute-call metrics.

- [ ] **Step 3: Run the focused tests and confirm they fail for the missing CLI helpers/fields**

Run:

```bash
pytest -q tests/test_hosting_capacity_cli.py -k 'cli_resource_metrics or prints_json_summary or returns_interrupted'
```

Expected: failures reporting that the new helper names or JSON fields are not yet available.

### Task 2: Implement compute-scoped CLI resource metrics

**Files:**
- Modify: `src/ercot_game/hosting_capacity_cli.py`

- [ ] **Step 1: Add the optional resource import and helpers**

After the standard-library imports, add:

```python
try:
    import resource
except ImportError:  # pragma: no cover - Windows
    resource = None
```

Add helpers matching the benchmark semantics:

```python
def _resource_usage_snapshot() -> tuple[int | None, int | None]:
    if resource is None or not hasattr(resource, "RUSAGE_SELF"):
        return (None, None)

    def read_usage(kind) -> int | None:
        try:
            raw = int(resource.getrusage(kind).ru_maxrss)
        except (AttributeError, OSError, TypeError, ValueError):
            return None
        return raw if raw >= 0 else None

    self_rss = read_usage(resource.RUSAGE_SELF)
    child_rss = (
        read_usage(resource.RUSAGE_CHILDREN)
        if hasattr(resource, "RUSAGE_CHILDREN")
        else None
    )
    return (self_rss, child_rss)


def _rss_mb(raw: int) -> float:
    divisor = 1024 * 1024 if sys.platform == "darwin" else 1024
    return round(raw / divisor, 3)


def _peak_rss_mb(after: tuple[int | None, int | None]) -> float | None:
    values = [value for value in after if value is not None and value > 0]
    return _rss_mb(max(values)) if values else None


def _rss_delta_mb(
    before: tuple[int | None, int | None], after: tuple[int | None, int | None]
) -> float | None:
    deltas = [
        after_value - before_value
        for before_value, after_value in zip(before, after)
        if before_value is not None
        and after_value is not None
        and after_value >= before_value
    ]
    return _rss_mb(max(deltas)) if deltas else None
```

- [ ] **Step 2: Thread metrics through `_summary` and `main`**

Extend `_summary` with `peak_rss_mb` and `rss_delta_mb` keyword arguments and include the exact top-level keys in its returned dictionary. In `main`, set `rss_before = _resource_usage_snapshot()` immediately before the `compute_hosting_capacity_matrix` call, set `rss_after = _resource_usage_snapshot()` immediately after it, and compute the two values for `_summary`.

Initialize `rss_before` before the `try` block or initialize both values to `None`-equivalents so the interrupt path is safe if interruption occurs before the compute snapshot. In `KeyboardInterrupt`, take a fresh `_resource_usage_snapshot()` as the post/interruption snapshot, derive metrics from the before snapshot, and include both fields in `partial`. Do not change return codes or existing keys.

- [ ] **Step 3: Run the focused tests and confirm they pass**

Run:

```bash
pytest -q tests/test_hosting_capacity_cli.py -k 'cli_resource_metrics or prints_json_summary or returns_interrupted'
```

Expected: all selected tests pass.

### Task 3: Update pilot documentation

**Files:**
- Modify: `docs/hosting_capacity/README.md`
- Modify: `docs/hosting_capacity/benchmark-report.md`

- [ ] **Step 1: Clarify the pilot criteria**

Update each pilot-gate criteria section so it says the CLI JSON summary records both `peak_rss_mb` and `rss_delta_mb` for every normal or interrupted run, while the independent raw `/usr/bin/time -l` logs are retained as raw evidence. Preserve the existing requirement to record the values and do not remove the before/after RSS evidence language.

- [ ] **Step 2: Run documentation contract tests**

Run:

```bash
pytest -q tests/test_hosting_capacity_documentation.py
```

Expected: all documentation checks pass.

### Task 4: Full verification and commit

**Files:**
- Verify: `src/ercot_game/hosting_capacity_cli.py`
- Verify: `tests/test_hosting_capacity_cli.py`
- Verify: `docs/hosting_capacity/README.md`
- Verify: `docs/hosting_capacity/benchmark-report.md`

- [ ] **Step 1: Run the complete relevant test files**

Run:

```bash
pytest -q tests/test_hosting_capacity_cli.py tests/test_hosting_capacity_documentation.py
```

Expected: exit code 0 with zero failures.

- [ ] **Step 2: Inspect the diff and verify compatibility requirements**

Run:

```bash
git diff --check
git diff -- src/ercot_game/hosting_capacity_cli.py tests/test_hosting_capacity_cli.py docs/hosting_capacity/README.md docs/hosting_capacity/benchmark-report.md
```

Confirm both summary paths retain all pre-existing fields and exit codes, and that no `/usr/bin/time -l` command was removed or made dependent on the CLI metrics.

- [ ] **Step 3: Commit the implementation**

Run:

```bash
git add src/ercot_game/hosting_capacity_cli.py tests/test_hosting_capacity_cli.py docs/hosting_capacity/README.md docs/hosting_capacity/benchmark-report.md
git commit -m "Add auditable CLI resource metrics"
```
