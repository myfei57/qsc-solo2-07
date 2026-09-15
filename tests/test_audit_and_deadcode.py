"""审计留存与死代码门禁。"""

from __future__ import annotations

import os
import subprocess
import sys

from fgd.errors import InterlockError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_alarm_entries_are_captured(platform):
    platform.start()
    platform.safety.check(concentration_ppm=60.0, pressure_kpa=0.42)
    alarms = platform.audit.alarms()
    assert alarms
    assert any(item["severity"] == "alarm" for item in alarms)
    assert platform.audit.entries(limit=5)
    assert platform.audit.tail(3)[0]["ts"] >= platform.audit.tail(3)[-1]["ts"]
    assert platform.audit.replay_from_journal()[0]["component"]
    assert platform.audit.replay_segment(0)


def test_platform_error_is_recorded_with_component(platform):
    try:
        platform.fan.require_durable()
    except InterlockError as error:
        record = platform.note_error(error)
    assert record["component"] == "fan"
    assert record["action"] == "error"
    assert record["severity"] == "warning"


def test_events_are_published_to_subscribers(platform):
    seen: list[str] = []
    unsubscribe = platform.bus.subscribe("fan", lambda event: seen.append(event.action))
    platform.fan.ramp_to_negative_pressure()
    assert "ramp_start" in seen and "ramp_done" in seen
    unsubscribe()
    assert platform.bus.history("fan")


def test_deadcode_gate_reports_no_unused_symbols():
    result = subprocess.run(
        [sys.executable, os.path.join(ROOT, "tools", "deadcode.py"), os.path.join(ROOT, "fgd")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"unused_symbols": []' in result.stdout
    assert '"unreachable_modules": []' in result.stdout
