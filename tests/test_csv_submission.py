from __future__ import annotations

import pytest

from ercot_game.csv_submission import (
    CSV_COLUMNS,
    load_generator_costs,
    load_bus_ids,
    read_student_csv,
)


def _write(path, rows: str) -> None:
    path.write_text(",".join(CSV_COLUMNS) + "\n" + rows, encoding="utf-8")


def test_load_bus_ids_accepts_full_prepared_bus_table(tmp_path):
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    (artifact / "bus.csv").write_text(
        "bus_id,type,Pd,Qd,Gs,Bs,zone_id,Vm,Va,baseKV,loss_zone,Vmax,Vmin,"
        "lam_P,lam_Q,mu_Vmax,mu_Vmin,interconnect\n"
        "3001001,1,20.78,5.89,0,0,301,0.998372,-22.3462,115,9,1.1,0.9,"
        "0,0,0,0,Texas\n"
        "3001002,1,15.41,4.37,0,0,301,1.02291,-17.8585,115,9,1.1,0.9,"
        "0,0,0,0,Texas\n",
        encoding="utf-8",
    )

    assert load_bus_ids(artifact) == frozenset({"3001001", "3001002"})


@pytest.mark.parametrize(
    ("catalog", "message"),
    (
        ("type,Pd\n1,20\n", "must contain a bus_id column"),
        ("bus_id,type\n,1\n", "row 2: bus_id is required"),
        ("bus_id,type\n3001001,1\n,1\n", "row 3: bus_id is required"),
    ),
    ids=("missing-column", "empty-only-id", "empty-id-row"),
)
def test_load_bus_ids_rejects_missing_or_empty_ids(tmp_path, catalog, message):
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    (artifact / "bus.csv").write_text(catalog, encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_bus_ids(artifact)


def test_csv_submission_uses_source_bus_ids_and_reconstructs_profile(tmp_path):
    path = tmp_path / "team.csv"
    _write(
        path,
        "team_1,week_1,1,3002041,40,G1,20,,,\n"
        "team_1,week_1,2,3002041,20,G1,20,,,\n",
    )

    submission = read_student_csv(
        path,
        bus_ids={"3002041"},
        horizon=2,
    )

    assert submission.team_id == "team_1"
    assert submission.submission_id == "week_1"
    assert submission.plan.connections[0].bus_id == "3002041"
    assert submission.plan.connections[0].capacity_mw == 40.0
    assert submission.plan.connections[0].profile == (1.0, 0.5)
    assert submission.plan.generators[0].capacity_mw == 20.0


def test_generator_operating_cost_is_inherited_from_dataset_catalog(tmp_path):
    path = tmp_path / "team.csv"
    _write(path, "team_1,week_1,1,3002041,40,G1,20,,,\n")

    submission = read_student_csv(
        path,
        bus_ids={"3002041"},
        generator_cost_by_id={"G1": 42.5},
        horizon=1,
    )

    assert submission.plan.generators[0].marginal_cost_per_mwh == 42.5


def test_csv_submission_can_require_complete_horizon(tmp_path):
    path = tmp_path / "team.csv"
    _write(path, "team_1,week_1,1,3002041,40,,,,,\n")

    with pytest.raises(ValueError, match="must cover every period"):
        read_student_csv(
            path,
            bus_ids={"3002041"},
            horizon=2,
            require_complete_horizon=True,
        )


def test_csv_submission_rejects_unknown_bus_and_duplicate_key(tmp_path):
    path = tmp_path / "team.csv"
    _write(path, "team_1,week_1,1,UNKNOWN,40,,,,,\n")
    with pytest.raises(ValueError, match="unknown scenario bus"):
        read_student_csv(path, bus_ids={"3002041"}, horizon=1)

    _write(
        path,
        "team_1,week_1,1,3002041,40,,,,,\n"
        "team_1,week_1,1,3002041,40,,,,,\n",
    )
    with pytest.raises(ValueError, match="duplicate submission key"):
        read_student_csv(path, region_to_source_bus={"PANHANDLE": "3002041"}, horizon=1)


def test_csv_submission_accepts_team_owned_storage(tmp_path):
    path = tmp_path / "team.csv"
    _write(path, "team_1,week_1,1,3002041,40,,,S1,10,20\n")
    submission = read_student_csv(path, bus_ids={"3002041"}, horizon=1)
    assert submission.plan.storages[0].storage_id == "S1"
    assert submission.plan.storages[0].power_mw == 10.0
    assert submission.plan.storages[0].energy_mwh == 20.0


@pytest.mark.parametrize(
    "row",
    (
        "team_1,week_1,1,PANHANDLE,40,,,,",
        "team_1,week_1,1,PANHANDLE,40,,,,,,unexpected",
    ),
    ids=("truncated", "extra-fields"),
)
def test_csv_submission_rejects_rows_with_wrong_field_count(tmp_path, row):
    path = tmp_path / "team.csv"
    _write(path, row + "\n")

    with pytest.raises(ValueError, match=rf"row 2: expected exactly {len(CSV_COLUMNS)} fields"):
        read_student_csv(path, bus_ids={"3002041"}, horizon=1)


def test_csv_submission_rejects_malformed_quoting(tmp_path):
    path = tmp_path / "malformed.csv"
    path.write_text(
        ",".join(CSV_COLUMNS) + "\n"
        'team_1,week_1,1,3002041,"40,G1,20,,,,\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="malformed CSV"):
        read_student_csv(path, bus_ids={"3002041"}, horizon=1)


def test_csv_submission_rejects_oversized_field_as_malformed_csv(tmp_path):
    path = tmp_path / "oversized.csv"
    path.write_text(
        ",".join(CSV_COLUMNS) + "\n"
        + ("team_1,week_1,1,3002041," + ("9" * 131073) + ",,,,,\n"),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="malformed CSV"):
        read_student_csv(path, bus_ids={"3002041"}, horizon=1)


@pytest.mark.parametrize("cost", ("nan", "inf", "-1"))
def test_generator_cost_catalog_rejects_invalid_costs(tmp_path, cost):
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    (artifact / "generator_limits.csv").write_text(
        "generator_id,marginal_cost_c1_per_mwh\n" f"G1,{cost}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="generator catalog.*finite and non-negative"):
        load_generator_costs(artifact)
