from ercot_game.models import SitePlan, TeamPlan


def test_site_plan_uses_constant_site_capacities_without_hourly_profile() -> None:
    site = SitePlan(
        site_id="site-1",
        bus_id="b0001",
        connection_capacity_mw=40.0,
        generator_capacity_mw=10.0,
        storage_power_mw=5.0,
        storage_energy_mwh=20.0,
    )

    assert site.site_id == "site-1"
    assert site.bus_id == "b0001"
    assert site.connection_capacity_mw == 40.0
    assert site.generator_capacity_mw == 10.0
    assert site.storage_power_mw == 5.0
    assert site.storage_energy_mwh == 20.0
    assert not hasattr(site, "profile")


def test_team_plan_accepts_site_only_schema_and_freezes_site_sequence() -> None:
    site = SitePlan("site-1", "b0001", 40.0)

    plan = TeamPlan(team_id="team-a", sites=[site])

    assert plan.connections == ()
    assert plan.sites == (site,)
