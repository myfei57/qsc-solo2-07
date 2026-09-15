"""并发仲裁：两个调节回路共享一个执行器时必须收敛到唯一值。"""

from __future__ import annotations

import threading

import pytest

from fgd.ammonia.valve import AmmoniaValve
from fgd.fan.speed import SpeedArbiter


def test_speed_arbiter_converges_under_parallel_writers(platform):
    errors: list[BaseException] = []

    def writer(loop, rpm, priority):
        try:
            for _ in range(120):
                platform.arbiter.request(loop, rpm, priority)
                platform.arbiter.resolve()
        except BaseException as exc:  # pragma: no cover - 线程内异常收集
            errors.append(exc)

    threads = [
        threading.Thread(target=writer, args=("boiler_draft", 1120.0, 60)),
        threading.Thread(target=writer, args=("fgd_pressure", 780.0, 40)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    outcome = platform.arbiter.resolve()
    assert outcome.rpm == pytest.approx(1120.0)
    assert platform.arbiter.committed_rpm() == pytest.approx(1120.0)
    assert platform.arbiter.snapshot()["committed_rpm"] == pytest.approx(1120.0)


def test_valve_safety_priority_beats_nox_loop_under_parallel_writers(platform):
    errors: list[BaseException] = []

    def writer(label, rate, priority):
        try:
            for _ in range(120):
                platform.valve.request(label, rate, priority)
                platform.valve.resolve()
        except BaseException as exc:  # pragma: no cover - 线程内异常收集
            errors.append(exc)

    threads = [
        threading.Thread(target=writer, args=("nox_loop", 260.0, AmmoniaValve.NOX_LOOP_PRIORITY)),
        threading.Thread(target=writer, args=("safety", 375.0, AmmoniaValve.SAFETY_PRIORITY)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    outcome = platform.valve.resolve()
    assert outcome.rate_kg_h == pytest.approx(375.0)
    assert platform.valve.committed_rate() == pytest.approx(375.0)


def test_safety_check_trips_supply_and_preempts_valve(platform):
    platform.start()
    outcome = platform.safety.check(concentration_ppm=48.0, pressure_kpa=0.42)
    assert outcome is not None
    assert outcome.winner == "safety"
    assert platform.safety.tripped is True
    assert platform.supply.is_established() is False
    assert platform.safety.safe_cap() == pytest.approx(375.0)


def test_safety_recovers_and_resets_supply(platform):
    platform.start()
    platform.safety.check(concentration_ppm=48.0, pressure_kpa=0.42)
    assert platform.safety.check(concentration_ppm=3.0, pressure_kpa=0.42) is None
    assert platform.safety.tripped is False
    assert platform.supply.is_established() is True
    assert platform.safety.checks() >= 2


def test_arbiter_rejects_unknown_loops_and_clamps(platform):
    arbiter: SpeedArbiter = platform.arbiter
    request = arbiter.request("fgd_pressure", 99_999.0, 40, reason="over-range")
    assert request.rpm == pytest.approx(arbiter.snapshot()["max_rpm"])
    assert arbiter.snapshot()["pending"][0]["reason"] == "over-range"
