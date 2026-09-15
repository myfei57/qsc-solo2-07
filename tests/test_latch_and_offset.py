"""pH 闩锁与分析仪标定基线。"""

from __future__ import annotations

import pytest

from fgd.app import CycleInputs


def _idle_inputs(load: float = 0.9) -> CycleInputs:
    return CycleInputs(load=load, nox_target=42.0, ammonia_concentration_ppm=4.0, dt=1.0)


def drive_ph_down(platform, target: float) -> float:
    """沿真实路径把浆液 pH 拉到目标值：不脱硫只吸收，碱度自然被消耗。"""

    guard = 0
    while platform.slurry.ph > target and guard < 400:
        platform.slurry.absorb_so2(1.0, 0.0)
        guard += 1
    return platform.slurry.ph


def test_latch_sets_below_low_threshold_and_resets_on_recovery(platform):
    latch = platform.latch
    assert latch.observe(5.9) is False
    assert latch.observe(5.05) is True
    assert latch.is_blocking() is True
    assert latch.observe(5.30) is True
    assert latch.observe(5.62) is False
    assert latch.is_blocking() is False
    assert latch.counts() == {"sets": 1, "resets": 1}


def test_blocked_latch_holds_lime_feed_then_resumes(platform):
    platform.start()
    ph = drive_ph_down(platform, 5.05)
    blocked = platform.circulation.step(ph)
    assert blocked["latched"] is True
    assert blocked["fed_pct"] == pytest.approx(0.0)
    assert platform.lime.blocked_count() >= 1

    platform.slurry.add_lime(6.0)
    resumed = platform.circulation.step(platform.slurry.ph)
    assert resumed["latched"] is False
    assert resumed["fed_pct"] > 0.0


def test_low_ph_is_visible_at_the_stack(platform):
    platform.start()
    for _ in range(3):
        platform.cycle(_idle_inputs(0.9))
    healthy = platform.status()["emission"]
    assert healthy["compliant"] is True

    drive_ph_down(platform, 5.05)
    report = platform.cycle(_idle_inputs(0.9))
    assert "so2" in report["emission"]["breach"]


def test_manual_latch_reset_is_recorded(platform):
    platform.latch.observe(5.0)
    assert platform.latch.reset() is True
    assert platform.latch.reset() is False
    assert platform.latch.snapshot()["latched"] is False


def test_recalibration_updates_epoch_and_judgement(platform):
    platform.start()
    platform.cycle(_idle_inputs(0.9))
    before = platform.status()["emission"]

    calibration = platform.calibration_probe(offset_mg_m3=8.0)
    assert calibration["epoch_after"] == calibration["epoch_before"] + 1
    assert platform.offset.current() == pytest.approx(8.0)

    report = platform.cycle(_idle_inputs(0.9))
    assert report["emission"]["offset_mg_m3"] == pytest.approx(8.0)
    assert report["emission"]["compliant"] is True
    assert before["compliant"] is True


def test_offset_reset_returns_to_default(platform):
    platform.offset.calibrate(12.0, platform.clock.now())
    assert platform.offset.reset() == pytest.approx(0.0)
    assert platform.offset.age_s(platform.clock.now()) == pytest.approx(0.0)
