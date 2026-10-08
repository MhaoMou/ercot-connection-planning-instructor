from __future__ import annotations

import csv
import json
from pathlib import Path

from tools.activsg2000.build_scenario_package import build_package


def _source_fixture(root: Path) -> Path:
    root.mkdir()
    (root / "bus.csv").write_text(
        "BUS_I,BUS_TYPE,PD,QD,GS,BS,BUS_AREA,VM,VA,BASE_KV,ZONE,VMAX,VMIN,LAM_P,LAM_Q,MU_VMAX,MU_VMIN\n"
        "1,3,100,20,0,0,1,1,0,230,1,1.1,0.9,0,0,0,0\n"
        "2,1,80,15,0,0,1,1,0,230,1,1.1,0.9,0,0,0,0\n",
        encoding="utf-8",
    )
    (root / "branch.csv").write_text(
        "F_BUS,T_BUS,BR_R,BR_X,BR_B,RATE_A,RATE_B,RATE_C,TAP,SHIFT,BR_STATUS,ANGMIN,ANGMAX,PF,QF,PT,QT,MU_SF,MU_ST,MU_ANGMIN,MU_ANGMAX\n"
        "1,2,0.01,0.1,0,200,0,0,0,0,1,0,0,0,0,0,0,0,0,0,0\n",
        encoding="utf-8",
    )
    (root / "gen.csv").write_text(
        "GEN_BUS,PG,QG,QMAX,QMIN,VG,MBASE,GEN_STATUS,PMAX,PMIN,PC1,PC2,QC1MIN,QC1MAX,QC2MIN,QC2MAX,RAMP_AGC,RAMP_10,RAMP_30,RAMP_Q,APF,MU_PMAX,MU_PMIN,MU_QMAX,MU_QMIN\n"
        "1,100,0,100,-100,1,100,1,200,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0\n"
        "2,50,0,100,-100,1,100,1,100,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0\n",
        encoding="utf-8",
    )
    (root / "gencost.csv").write_text(
        "MODEL,STARTUP,SHUTDOWN,NCOST,C2,C1,C0\n2,0,0,3,0,10,0\n2,0,0,3,0,20,0\n",
        encoding="utf-8",
    )
    (root / "case.csv").write_text("CASENAME,VERSION,BASE_MVA\nACTIVSg2000,2,100\n", encoding="utf-8")
    return root


def test_builder_writes_deterministic_scenario_recipe(tmp_path: Path) -> None:
    output = tmp_path / "package"
    build_package(_source_fixture(tmp_path / "source"), output, enforce_activsg2000_size=False)
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["package_version"] == "activsg2000-scenarios-v1"
    assert manifest["validation_status"] == "pending"
    assert set(manifest["blocks"]) == {
        "2027-jan_jun", "2027-jul_dec", "2028-jan_jun",
        "2028-jul_dec", "2029-jan_jun", "2029-jul_dec",
    }
    with (output / "network" / "generator_catalog.csv").open() as handle:
        assert len(list(csv.DictReader(handle))) == 2
    profiles = json.loads((output / "profile_parameters.json").read_text(encoding="utf-8"))
    assert len(profiles["generators"]) == 2


def test_builder_uses_dataset_static_generator_limits_when_source_has_no_availability_data(
    tmp_path: Path,
) -> None:
    output = tmp_path / "package"
    build_package(_source_fixture(tmp_path / "source"), output, enforce_activsg2000_size=False)
    profiles = json.loads((output / "profile_parameters.json").read_text(encoding="utf-8"))

    assert profiles["generator_type_source"] == "ACTIVSg2000 generator table"
    assert profiles["availability_model"] == "static status and nameplate limits"
    assert all(row["profile_type"] == "thermal" for row in profiles["generators"])
