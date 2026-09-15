"""启动顺序：每一步的硬前置条件都必须真实生效。"""

from __future__ import annotations

import pytest

from fgd.app import CycleInputs
from fgd.denox.inject import AmmoniaInjector
from fgd.errors import InterlockError, SequenceError
from fgd.esp.admit import admit_flue
from fgd.scrubber.loop import SlurryLoop
from fgd.stack.opening import BoilerFlue, open_boiler_flue


def test_startup_sequence_order_is_compliant(platform):
    report = platform.start()
    assert report["violations"] == []
    assert report["topology_missing"] == []
    assert report["steps"].index("fan_persist") < report["steps"].index("scrubber_loop")
    assert report["steps"].index("ammonia_supply") < report["steps"].index("denox_inject")
    assert report["steps"].index("esp_energize") < report["steps"].index("esp_admit")
    assert report["steps"].index("oxid_start") < report["steps"].index("scrubber_loop")


def test_flue_damper_refuses_to_open_without_negative_pressure(platform):
    flue = BoilerFlue()
    with pytest.raises(InterlockError):
        open_boiler_flue(platform.fan, flue, platform.bus)
    assert flue.is_open is False


def test_slurry_loop_requires_durable_fan_state(platform):
    loop = SlurryLoop(platform.bus, 9600.0)
    platform.blower.start()
    with pytest.raises(InterlockError):
        loop.start(platform.blower, platform.fan)
    assert loop.running is False
    checks = loop.stage_check(platform.blower, platform.fan)
    assert checks["fan_durable"] is False
    assert checks["oxid_running"] is True


def test_slurry_loop_requires_running_oxidation_blower(platform):
    platform.fan.ramp_to_negative_pressure()
    platform.fan.persist_state()
    loop = SlurryLoop(platform.bus, 9600.0)
    with pytest.raises(InterlockError):
        loop.start(platform.blower, platform.fan)
    assert loop.running is False


def test_injector_refuses_to_start_before_ammonia_supply(platform):
    injector = AmmoniaInjector(platform.bus, platform.catalyst, 35.0)
    with pytest.raises(InterlockError):
        injector.start_injection(platform.supply)
    assert injector.running is False
    assert platform.catalyst.efficiency() == pytest.approx(0.90)


def test_esp_must_energize_before_flue_is_admitted(platform):
    platform.fan.ramp_to_negative_pressure()
    platform.fan.persist_state()
    with pytest.raises(InterlockError):
        admit_flue(platform.esp, platform.fan, platform.bus)
    platform.esp.energize(55.0)
    payload = admit_flue(platform.esp, platform.fan, platform.bus)
    assert payload["voltage_kv"] == pytest.approx(55.0)
    assert platform.fan.phase.value == "admitted"


def test_missing_ammonia_pressure_is_rejected(platform):
    with pytest.raises(InterlockError):
        platform.supply.establish(0.05)
    assert platform.supply.is_established() is False
    assert platform.supply.establish(0.42) == pytest.approx(0.42)
    assert platform.supply.is_established() is True


def test_cycle_refuses_to_regulate_before_startup(platform):
    """未启动时推进周期必须被拒绝，而不是带着未投运的设备继续调节。"""

    with pytest.raises(SequenceError):
        platform.cycle(
            CycleInputs(
                load=0.9, nox_target=42.0, ammonia_concentration_ppm=4.0, dt=1.0
            )
        )


def test_stopped_slurry_loop_leaves_the_tower_empty(platform):
    """泵停运时吸收塔按空塔处理，SO2 直接穿透到烟囱。"""

    result = platform.absorber.absorb(2000.0, 5.75, 9600.0, loop_running=False)
    assert result.removal == pytest.approx(0.0)
    assert result.so2_out_mg_m3 == pytest.approx(2000.0)
    assert result.as_dict()["loop_running"] is False


def test_speed_arbiter_prefers_higher_priority_loop(platform):
    with pytest.raises(ValueError):
        SlurryLoop(platform.bus, 0.0)
    outcome = platform.concurrency_probe(rounds=6)
    assert outcome["speed_winner"] == "boiler_draft"
    assert outcome["valve_winner"] == "safety"


def test_sequence_error_type_matches_contract(platform):
    loop = SlurryLoop(platform.bus, 9600.0)
    assert isinstance(SequenceError("x", component="scrubber"), Exception)
    assert loop.flow_m3_h == 0.0
