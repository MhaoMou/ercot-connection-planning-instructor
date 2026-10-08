from __future__ import annotations

import pytest

from ercot_game.csv_submission import (
    BUS_CSV_COLUMNS,
    CANONICAL_CSV_COLUMNS,
    CSV_COLUMNS,
    LEGACY_CSV_COLUMNS,
    load_generator_costs,
    load_bus_ids,
    read_legacy_student_csv,
    read_student_csv,
)


CANONICAL_COLUMNS = (
    "team_id",
    "site_id",
    "bus_id",
    "connection_capacity_mw",
    "generator_capacity_mw",
    "storage_power_mw",
    "storage_energy_mwh",
)


def test_public_csv_column_names_share_the_canonical_seven_column_contract():
    assert CANONICAL_CSV_COLUMNS == CANONICAL_COLUMNS
    assert CSV_COLUMNS is CANONICAL_CSV_COLUMNS
    assert BUS_CSV_COLUMNS is CANONICAL_CSV_COLUMNS


def _write(path, rows: str) -> None:
    path.write_text(",".join(LEGACY_CSV_COLUMNS) + "\n" + rows, encoding="utf-8")


def _write_canonical(path, rows: list[list[object]]) -> None:
    path.write_text(
        ",".join(CANONICAL_COLUMNS)
        + "\n"
        + "\n".join(",".join(str(value) for value in row) for row in rows)
        + "\n",
        encoding="utf-8",
    )


def test_canonical_csv_parses_multiple_sites_and_assets_without_submission_id(tmp_path):
    path = tmp_path / "team.csv"
    _write_canonical(
        path,
        [
            ["team_1", "site_north", "3002041", "40", "20", "10", "40"],
            ["team_1", "site_south", "3002042", "15", "0", "0", "0"],
        ],
    )

    submission = read_student_csv(path, bus_ids={"3002041", "3002042"})

    assert submission.team_id == "team_1"
    assert submission.submission_id is None
    assert [(site.site_id, site.bus_id) for site in submission.plan.sites] == [
        ("site_north", "3002041"),
        ("site_south", "3002042"),
    ]
    assert submission.plan.sites[0].generator_capacity_mw == 20.0
    assert submission.plan.sites[0].storage_energy_mwh == 40.0
    assert len(submission.plan.generators) == 1
    assert len(submission.plan.storages) == 1


@pytest.mark.parametrize(
    ("header", "message"),
    (
        ("team_id,site_id,bus_id,connection_capacity_mw,generator_capacity_mw,storage_power_mw", "header must be exactly"),
        ("team_id,site_id,bus_id,connection_capacity_mw,generator_capacity_mw,storage_power_mw,storage_energy_mwh,extra", "header must be exactly"),
        ("team_id,submission_id,period,site_id,load_mw,generator_id,generator_capacity_mw,storage_id,storage_power_mw,storage_energy_mwh", "header must be exactly"),
    ),
    ids=("missing-column", "extra-column", "legacy-header"),
)
def test_canonical_csv_rejects_missing_extra_or_legacy_columns(tmp_path, header, message):
    path = tmp_path / "bad.csv"
    path.write_text(header + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        read_student_csv(path)


def test_canonical_csv_rejects_duplicate_site_rows(tmp_path):
    path = tmp_path / "duplicate.csv"
    _write_canonical(
        path,
        [
            ["team_1", "site_1", "3002041", "40", "0", "0", "0"],
            ["team_1", "site_1", "3002042", "40", "0", "0", "0"],
        ],
    )

    with pytest.raises(ValueError, match="duplicate site_id"):
        read_student_csv(path)


@pytest.mark.parametrize(
    ("row", "message"),
    (
        (["team 1", "site_1", "3002041", "40", "0", "0", "0"], "team_id.*malformed"),
        (["team_1", "site 1", "3002041", "40", "0", "0", "0"], "site_id.*malformed"),
        (["team_1", "site_1", "", "40", "0", "0", "0"], "bus_id is required"),
    ),
    ids=("team-whitespace", "site-whitespace", "empty-bus"),
)
def test_canonical_csv_rejects_malformed_ids(tmp_path, row, message):
    path = tmp_path / "bad-id.csv"
    _write_canonical(path, [row])

    with pytest.raises(ValueError, match=message):
        read_student_csv(path)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("connection_capacity_mw", "not-a-number", "connection_capacity_mw.*finite decimal"),
        ("generator_capacity_mw", "-1", "generator_capacity_mw.*non-negative"),
        ("storage_power_mw", "nan", "storage_power_mw.*finite"),
        ("storage_energy_mwh", "inf", "storage_energy_mwh.*finite"),
    ),
)
def test_canonical_csv_rejects_bad_capacity_values(tmp_path, field, value, message):
    path = tmp_path / "bad-capacity.csv"
    row = ["team_1", "site_1", "3002041", "40", "0", "0", "0"]
    row[list(CANONICAL_COLUMNS).index(field)] = value
    _write_canonical(path, [row])

    with pytest.raises(ValueError, match=message):
        read_student_csv(path)


def test_canonical_csv_rejects_generator_above_connection_capacity(tmp_path):
    path = tmp_path / "bad-generator.csv"
    _write_canonical(path, [["team_1", "site_1", "3002041", "10", "10.1", "0", "0"]])

    with pytest.raises(ValueError, match="generator capacity.*connection capacity"):
        read_student_csv(path)


@pytest.mark.parametrize(
    ("power", "energy", "message"),
    (
        ("0", "4", "storage power and energy must both be zero or positive"),
        ("10", "0", "storage power and energy must both be zero or positive"),
        ("10", "39", "storage energy must equal four times storage power"),
        ("-1", "-4", "storage_power_mw must be non-negative"),
    ),
)
def test_canonical_csv_rejects_invalid_storage_dimensions(tmp_path, power, energy, message):
    path = tmp_path / "bad-storage.csv"
    _write_canonical(path, [["team_1", "site_1", "3002041", "40", "0", power, energy]])

    with pytest.raises(ValueError, match=message):
        read_student_csv(path)


def test_canonical_csv_rejects_malformed_quoting_and_oversized_fields(tmp_path):
    malformed = tmp_path / "malformed.csv"
    malformed.write_text(
        ",".join(CANONICAL_COLUMNS) + "\n"
        + 'team_1,site_1,3002041,"40,0,0,0\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="malformed CSV"):
        read_student_csv(malformed)

    oversized = tmp_path / "oversized.csv"
    oversized.write_text(
        ",".join(CANONICAL_COLUMNS) + "\n"
        + "team_1,site_1,3002041," + ("9" * 131073) + ",0,0,0\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="malformed CSV"):
        read_student_csv(oversized)


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

    submission = read_legacy_student_csv(
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


def test_team_generator_uses_common_operating_curve_compatibility_value(tmp_path):
    path = tmp_path / "team.csv"
    _write(path, "team_1,week_1,1,3002041,40,G1,20,,,\n")

    submission = read_legacy_student_csv(
        path,
        bus_ids={"3002041"},
        generator_cost_by_id={"PUBLIC_G1": 42.5},
        horizon=1,
    )

    assert submission.plan.generators[0].marginal_cost_per_mwh == 16.0


def test_csv_submission_can_require_complete_horizon(tmp_path):
    path = tmp_path / "team.csv"
    _write(path, "team_1,week_1,1,3002041,40,,,,,\n")

    with pytest.raises(ValueError, match="must cover every period"):
        read_legacy_student_csv(
            path,
            bus_ids={"3002041"},
            horizon=2,
            require_complete_horizon=True,
        )


def test_csv_submission_rejects_unknown_bus_and_duplicate_key(tmp_path):
    path = tmp_path / "team.csv"
    _write(path, "team_1,week_1,1,UNKNOWN,40,,,,,\n")
    with pytest.raises(ValueError, match="unknown scenario bus"):
        read_legacy_student_csv(path, bus_ids={"3002041"}, horizon=1)

    _write(
        path,
        "team_1,week_1,1,3002041,40,,,,,\n"
        "team_1,week_1,1,3002041,40,,,,,\n",
    )
    with pytest.raises(ValueError, match="duplicate submission key"):
        read_legacy_student_csv(path, region_to_source_bus={"PANHANDLE": "3002041"}, horizon=1)


def test_csv_submission_accepts_team_owned_storage(tmp_path):
    path = tmp_path / "team.csv"
    _write(path, "team_1,week_1,1,3002041,40,,,S1,10,20\n")
    submission = read_legacy_student_csv(path, bus_ids={"3002041"}, horizon=1)
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

    with pytest.raises(ValueError, match=rf"row 2: expected exactly {len(LEGACY_CSV_COLUMNS)} fields"):
        read_legacy_student_csv(path, bus_ids={"3002041"}, horizon=1)


def test_csv_submission_rejects_malformed_quoting(tmp_path):
    path = tmp_path / "malformed.csv"
    path.write_text(
        ",".join(LEGACY_CSV_COLUMNS) + "\n"
        'team_1,week_1,1,3002041,"40,G1,20,,,,\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="malformed CSV"):
        read_legacy_student_csv(path, bus_ids={"3002041"}, horizon=1)


def test_csv_submission_rejects_oversized_field_as_malformed_csv(tmp_path):
    path = tmp_path / "oversized.csv"
    path.write_text(
        ",".join(LEGACY_CSV_COLUMNS) + "\n"
        + ("team_1,week_1,1,3002041," + ("9" * 131073) + ",,,,,\n"),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="malformed CSV"):
        read_legacy_student_csv(path, bus_ids={"3002041"}, horizon=1)


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
