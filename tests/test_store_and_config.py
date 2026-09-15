"""持久化、日志分段与配置校验。"""

from __future__ import annotations

import json
import os

import pytest

import yaml

from fgd.config import config_to_mapping, default_config, load_config
from fgd.errors import ConfigError, NamespaceError, StorageError
from fgd.fan.state import FanPhase
from fgd.ns.registry import NamespaceRegistry
from fgd.store.journal import JsonlJournal
from fgd.store.jsonfile import JsonStore
from fgd.telemetry import TrendBuffer


def test_json_store_round_trip_and_digest(tmp_path):
    store = JsonStore(str(tmp_path))
    payload = {"phase": "ready", "pressure_kpa": -0.85}
    digest = store.write("fan_state", payload)
    assert store.exists("fan_state") is True
    assert store.read("fan_state") == payload
    assert store.digest("fan_state") == digest
    assert store.read("missing", default={"ok": True}) == {"ok": True}
    assert store.remove("fan_state") is True
    assert store.remove("fan_state") is False
    assert store.digest("fan_state") == ""


def test_json_store_rejects_path_escape(tmp_path):
    store = JsonStore(str(tmp_path))
    with pytest.raises(StorageError):
        store.path_for("../escape")
    with pytest.raises(StorageError):
        store.path_for("")


def test_journal_rotates_and_reads_across_segments(tmp_path):
    journal = JsonlJournal(str(tmp_path), "audit", segment_limit=3)
    for index in range(8):
        journal.append({"index": index})
    assert journal.segment_count() >= 3
    records = journal.read_all()
    assert [record["index"] for record in records] == list(range(8))
    assert journal.read_segment(0)[0]["index"] == 0
    assert journal.read_segment(999) == []


def test_fan_state_survives_restart(tmp_path, clock):
    from fgd.app import build_platform

    storage = str(tmp_path / "state")
    first = build_platform(default_config(), clock=clock, storage_root=storage)
    first.start()
    snapshot = first.fan.snapshot()
    assert snapshot["durable"] is True

    second = build_platform(default_config(), clock=clock, storage_root=storage)
    assert second.fan.restored_from_disk is True
    assert second.fan.is_durable() is True
    # 落盘的是"已建立负压"这一事实，重启后相位是 ready，压力与落盘值一致。
    assert second.fan.phase is FanPhase.READY
    assert second.fan.pressure_kpa == pytest.approx(snapshot["pressure_kpa"])
    # 重启后可以直接接入烟气：负压事实已落盘，无需重新升压。
    second.esp.energize(55.0)
    second.flue.drive(65.0)
    assert second.fan.admit() == pytest.approx(snapshot["pressure_kpa"])


def test_config_yaml_round_trip(tmp_path):
    config = default_config()
    path = tmp_path / "fgd.yaml"
    path.write_text(yaml.safe_dump(config_to_mapping(config), allow_unicode=True), encoding="utf-8")
    loaded = load_config(str(path))
    assert loaded == config


def test_config_rejects_missing_and_invalid_values(tmp_path):
    with pytest.raises(ConfigError):
        load_config(str(tmp_path / "absent.yaml"))

    broken = config_to_mapping(default_config())
    del broken["limits"]["fan"]["max_speed_rpm"]
    path = tmp_path / "broken.yaml"
    path.write_text(yaml.safe_dump(broken, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(str(path))

    bad_ph = config_to_mapping(default_config())
    bad_ph["limits"]["scrubber"]["ph_low"] = 5.9
    path.write_text(yaml.safe_dump(bad_ph, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(str(path))


def test_repository_config_file_matches_defaults():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    loaded = load_config(os.path.join(root, "config", "fgd.yaml"))
    assert loaded == default_config()


def test_namespace_registry_detects_duplicates(tmp_path):
    registry = NamespaceRegistry("unit1")
    registry.register("scrubber", "loop", "circulation-pump")
    with pytest.raises(NamespaceError):
        registry.register("scrubber", "loop", "circulation-pump")
    with pytest.raises(NamespaceError):
        registry.resolve("fgd/unit1/scrubber/missing")
    assert registry.in_area("scrubber")[0].device == "loop"
    assert registry.unit == "unit1"
    assert registry.describe()[0]["path"].startswith("fgd/unit1/")


def test_trend_buffer_fills_gaps_and_windows():
    trend = TrendBuffer(period_s=5.0)
    trend.append("so2", 100.0, 10.0)
    trend.append("so2", 130.0, 20.0)
    filled = trend.fill_gaps("so2")
    assert filled == 5
    samples = trend.window("so2", 100.0, 130.0)
    assert len(samples) == 7
    assert samples[1].filled is True
    assert trend.average("so2", 100.0, 130.0) > 0
    assert trend.latest("so2").value == pytest.approx(20.0)
    assert trend.series_names() == ["so2"]
    assert trend.snapshot()["so2"][0]["value"] == pytest.approx(10.0)


def test_audit_journal_is_jsonl_on_disk(platform):
    platform.start()
    journal_dir = os.path.join(platform.store.root, "audit")
    files = [name for name in os.listdir(journal_dir) if name.endswith(".jsonl")]
    assert files
    with open(os.path.join(journal_dir, files[0]), "r", encoding="utf-8") as handle:
        first = json.loads(handle.readline())
    assert first["component"]
    assert platform.audit.summary()["total"] > 0
