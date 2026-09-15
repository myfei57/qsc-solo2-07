"""停运顺序：先停烟、再清烟、最后切浆液循环。"""

from __future__ import annotations

import pytest

from fgd.errors import SequenceError
from fgd.fan.clear import ClearToken
from fgd.scrubber.cut import cut_loop


def test_shutdown_order_is_compliant(platform):
    platform.start()
    report = platform.stop()
    assert report["violations"] == []
    steps = report["steps"]
    assert steps.index("boiler_flue_close") < steps.index("fan_clear")
    assert steps.index("fan_clear") < steps.index("scrubber_cut")
    assert report["loop_running"] is False
    assert report["drawer_density_pct"] == pytest.approx(0.0)
    assert report["sphere_voltage_kv"] == pytest.approx(0.0)


def test_cut_loop_refuses_without_clear_token(platform):
    platform.start()
    unclean = ClearToken(pressure_kpa=-0.6, hold_s=0.0, cleared=False)
    with pytest.raises(SequenceError):
        cut_loop(platform.loop, unclean, platform.bus)
    assert platform.loop.running is True


def test_fan_clear_returns_environment_pressure(platform):
    platform.start()
    token = platform.shutdown.run()["steps"]
    assert "fan_clear" in token
    assert platform.fan.pressure_kpa == pytest.approx(0.0)
    assert platform.fan.is_durable() is False
