from __future__ import annotations

import json

import pytest

from rl_gym.reporting import aggregate_records, main


def _record(
    *,
    policy="greedy",
    year=2029,
    block="jan_jun",
    team_count=1,
    reward=10.0,
    status="not_evaluated",
    terminated=True,
    truncated=False,
    steps=3,
    policy_calls=None,
    action_count=None,
    episode_latency=0.2,
    reset_latency=None,
    step_latency=None,
    digest="digest-a",
):
    if policy_calls is None:
        policy_calls = steps
    return {
        "policy": policy,
        "scenario_year": year,
        "scenario_block": block,
        "team_count": team_count,
        "reward": reward,
        "status": status,
        "terminated": terminated,
        "truncated": truncated,
        "release_manifest_digest": digest,
        "timing": {
            "steps": steps,
            "policy_calls": policy_calls,
            "episode_latency_seconds": episode_latency,
        },
        "aggregate": {"unknown_action_count": 1},
    }


def _add_timing(record, *, action_count=None, reset_latency=None, step_latency=None):
    if action_count is not None:
        record["timing"]["action_count"] = action_count
    if reset_latency is not None:
        record["timing"]["reset_latency_seconds"] = reset_latency
    if step_latency is not None:
        record["timing"]["step_latency_seconds"] = step_latency
    return record


def test_aggregate_groups_by_policy_year_block_and_team_count_and_summarizes_public_fields():
    records = [
        _record(reward=10.0, episode_latency=0.2, digest="digest-b"),
        _record(reward=14.0, episode_latency=0.4, year=2028, digest="digest-a"),
        _record(policy="random", reward=2.0, status="evaluated", terminated=False, truncated=True),
    ]

    report = aggregate_records(records)

    assert len(report["groups"]) == 3
    first = report["groups"][0]
    assert first["key"] == {
        "policy": "greedy",
        "scenario_year": 2028,
        "scenario_block": "jan_jun",
        "team_count": 1,
    }
    assert first["records"] == 1
    assert first["reward"] == {"mean": 14.0, "std": 0.0}
    assert first["termination_rates"] == {"terminated": 1.0, "truncated": 0.0}
    assert first["action_counts"] == {
        "policy_calls": 3,
        "action_count": 0,
        "steps": 3,
        "unknown_actions": 1,
    }
    assert first["latency_seconds"]["episode"]["mean"] == 0.4
    assert first["latency_seconds"]["episode"]["std"] == 0.0
    assert first["manifest_digests"] == ["digest-a"]

    same_year = [group for group in report["groups"] if group["key"]["scenario_year"] == 2029]
    assert same_year[0]["records"] == 1
    assert same_year[0]["status"]["not_evaluated"]["records"] == 1
    assert same_year[0]["status"]["evaluator_backed"]["records"] == 0


def test_mixed_status_report_keeps_evaluator_backed_summary_separate():
    report = aggregate_records([
        _record(reward=4.0, status="not_evaluated"),
        _record(reward=8.0, status="evaluated"),
        _record(reward=12.0, status="evaluation_error"),
    ])

    group = report["groups"][0]
    assert group["status"]["not_evaluated"] == {
        "records": 1,
        "reward": {"mean": 4.0, "std": 0.0},
    }
    assert group["status"]["evaluator_backed"] == {
        "records": 2,
        "reward": {"mean": 10.0, "std": 2.0},
    }
    assert group["status_values"] == ["evaluated", "evaluation_error", "not_evaluated"]


def test_single_policy_is_valid_and_empty_input_is_rejected():
    assert aggregate_records([_record()])["policies"] == ["greedy"]
    with pytest.raises(ValueError, match="non-empty"):
        aggregate_records([])


def test_explicit_action_count_is_aggregated_separately_from_policy_calls():
    record = _add_timing(_record(policy_calls=2, steps=3), action_count=7)

    group = aggregate_records([record])["groups"][0]

    assert group["action_counts"] == {
        "policy_calls": 2,
        "action_count": 7,
        "steps": 3,
        "unknown_actions": 1,
    }


def test_same_key_records_sum_each_action_count_field_independently():
    records = [
        _add_timing(_record(policy_calls=2, steps=3), action_count=7),
        _add_timing(_record(policy_calls=5, steps=4, reward=12.0), action_count=11),
    ]

    group = aggregate_records(records)["groups"][0]

    assert group["records"] == 2
    assert group["action_counts"] == {
        "policy_calls": 7,
        "action_count": 18,
        "steps": 7,
        "unknown_actions": 2,
    }


def test_multi_team_records_remain_in_distinct_groups():
    report = aggregate_records([_record(team_count=1), _record(team_count=2)])

    assert [group["key"]["team_count"] for group in report["groups"]] == [1, 2]


def test_latency_statistics_include_mean_std_min_and_max_for_available_fields():
    records = [
        _add_timing(_record(episode_latency=0.2), reset_latency=0.1, step_latency=0.4),
        _add_timing(_record(episode_latency=0.6), reset_latency=0.3, step_latency=0.8),
    ]

    latency = aggregate_records(records)["groups"][0]["latency_seconds"]

    assert latency["episode"] == pytest.approx({"mean": 0.4, "std": 0.2, "min": 0.2, "max": 0.6})
    assert latency["reset"] == pytest.approx({"mean": 0.2, "std": 0.1, "min": 0.1, "max": 0.3})
    assert latency["step"] == pytest.approx({"mean": 0.6, "std": 0.2, "min": 0.4, "max": 0.8})


def test_complete_execution_metric_set_is_reported():
    record = _add_timing(
        _record(steps=3, policy_calls=3),
        action_count=6,
        reset_latency=0.1,
        step_latency=0.4,
    )

    group = aggregate_records([record])["groups"][0]

    assert group["action_counts"] == {
        "policy_calls": 3,
        "action_count": 6,
        "steps": 3,
        "unknown_actions": 1,
    }
    assert set(group["latency_seconds"]) == {"reset", "step", "episode"}


def test_multiple_manifest_digests_are_retained():
    report = aggregate_records([_record(digest="digest-b"), _record(digest="digest-a")])

    assert report["groups"][0]["manifest_digests"] == ["digest-a", "digest-b"]


@pytest.mark.parametrize(
    "mutator, message",
    [
        (lambda record: record.pop("policy"), "policy"),
        (lambda record: record.__setitem__("scenario_year", True), "scenario_year"),
        (lambda record: record.__setitem__("reward", float("nan")), "reward"),
        (lambda record: record.__setitem__("terminated", 1), "terminated"),
        (lambda record: record.__setitem__("plan", {"private": True}), "private"),
        (lambda record: record.__setitem__("evaluator_callback", "secret"), "private"),
    ],
)
def test_validation_rejects_malformed_or_private_records(mutator, message):
    record = _record()
    mutator(record)
    with pytest.raises(ValueError, match=message):
        aggregate_records([record])


@pytest.mark.parametrize(
    "nested_field",
    [
        {"aggregate": {"evaluator_output": {"score": 10.0}}},
        {"timing": {"nested": {"private_payload": {"token": "secret"}}}},
    ],
)
def test_validation_rejects_nested_evaluator_and_private_payloads(nested_field):
    record = _record()
    for field, value in nested_field.items():
        record[field].update(value)

    with pytest.raises(ValueError, match="private field is not allowed"):
        aggregate_records([record])


def test_validation_allows_documented_sanitized_aggregate_keys():
    record = _record()
    record["aggregate"].update({
        "projection_applied": True,
        "projected_team_count": 1,
        "evaluated": False,
        "joint_evaluated": False,
        "counts": {"finished": 1, "valid": 1},
        "statuses": {"evaluation": "not_evaluated"},
        "timing": {"steps": 3},
        "manifest_digest": "digest-a",
    })

    report = aggregate_records([record])

    assert report["records"] == 1


@pytest.mark.parametrize(
    "sensitive_payload",
    [
        {"counts": {"projection_diagnostics": {"team-a": {"fraction": 0.5}}}},
        {"statuses": {"evaluator_output": {"score": 10.0}}},
        {"timing": {"raw_response": {"body": "secret"}}},
        {"counts": {"plans": {"team-a": ["private plan"]}}},
        {"statuses": {"private": {"solver": {"dispatch": "secret"}}}},
        {"timing": {"callback": {"value": "secret"}}},
    ],
)
def test_validation_rejects_sensitive_keys_nested_in_sanitized_containers(sensitive_payload):
    record = _record()
    record["aggregate"].update(sensitive_payload)

    with pytest.raises(ValueError, match="private field is not allowed"):
        aggregate_records([record])


def test_json_cli_reads_records_and_emits_compact_json(capsys):
    assert main(["--format", "json"], stdin_text=json.dumps([_record()])) == 0
    output = capsys.readouterr().out
    assert "\n" not in output
    assert json.loads(output)["groups"][0]["records"] == 1


def test_markdown_cli_emits_group_table(capsys):
    record = _add_timing(
        _record(policy_calls=2, steps=3, status="evaluated"),
        action_count=7,
        reset_latency=0.1,
        step_latency=0.4,
    )
    assert main(["--format", "markdown"], stdin_text=json.dumps([record])) == 0
    output = capsys.readouterr().out
    assert "| Policy | Scenario | Teams | Records | Reward mean ± std | Policy calls | Actions | Steps |" in output
    assert "greedy" in output
    assert "2029/jan_jun" in output
    assert "7" in output
    assert "episode mean/std/min/max=0.2/0/0.2/0.2s" in output
    assert "digest-a" in output
    assert "evaluated: 1 records, reward 10 ± 0" in output
