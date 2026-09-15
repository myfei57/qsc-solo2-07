"""控制台：四个页面与状态／趋势／告警接口必须真实可用。"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from fgd.console.server import ConsoleServer, probe_pages


def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=5.0) as response:
        return json.loads(response.read().decode("utf-8"))


def _post(url: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload or {}).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(request, timeout=5.0) as response:
        return json.loads(response.read().decode("utf-8"))


@pytest.fixture()
def server(platform):
    console = ConsoleServer(platform)
    base = console.start()
    yield console, base
    console.stop()


def test_all_pages_and_status_endpoint_respond(server):
    console, base = server
    results = probe_pages(base, console.platform.config.pages)
    assert all(item["ok"] for item in results.values())
    assert results["api/status"]["bytes"] > 1000


def test_status_payload_carries_components_and_topology(server):
    _console, base = server
    payload = _get(f"{base}/api/status")
    assert payload["project_id"] == "zxy-117-py"
    assert payload["fan"]["phase"] in {"stopped", "ready", "admitted", "clearing"}
    assert payload["topology"]["links"]
    assert payload["namespaces_by_area"]["scrubber"]
    assert "readiness" not in payload["endpoints"]


def test_start_step_stop_round_trip_over_http(server):
    _console, base = server
    started = _post(f"{base}/api/start")
    assert started["ok"] is True
    assert started["report"]["violations"] == []

    stepped = _post(f"{base}/api/step", {"load": 0.9, "nox_target": 42.0, "dt": 1.0})
    assert stepped["ok"] is True
    assert stepped["cycle"]["emission"]["compliant"] is True

    stopped = _post(f"{base}/api/stop")
    assert stopped["ok"] is True
    assert stopped["report"]["violations"] == []


def test_trend_endpoint_reports_average_and_latest(server):
    _console, base = server
    _post(f"{base}/api/start")
    _post(f"{base}/api/step", {"load": 0.8, "dt": 1.0})
    payload = _get(f"{base}/api/trend?window_s=600")
    assert payload["series"]["so2"]["latest"] is not None
    assert payload["series"]["so2"]["average"] > 0.0
    assert "filled_points" in payload["series"]["so2"]


def test_unknown_series_is_rejected(server):
    _console, base = server
    with pytest.raises(urllib.error.HTTPError) as info:
        _get(f"{base}/api/trend?series=unknown")
    assert info.value.code == 400


def test_alarms_and_audit_and_journal_endpoints(server):
    _console, base = server
    _post(f"{base}/api/start")
    alarms = _get(f"{base}/api/alarms")
    audit = _get(f"{base}/api/audit?limit=20")
    journal = _get(f"{base}/api/journal")
    assert "count" in alarms
    assert audit["entries"] and audit["recent"]
    assert journal["entries"]
    assert journal["entries"][0]["action"]


def test_readiness_and_maintenance_endpoints(server):
    _console, base = server
    readiness = _get(f"{base}/api/readiness")
    assert set(readiness["slurry_loop"]) >= {"fan_durable", "oxid_running"}
    regenerated = _post(f"{base}/api/maintenance/catalyst", {"reason": "单元验证"})
    assert regenerated["catalyst"]["efficiency_after"] == pytest.approx(0.90)


def test_unknown_path_returns_404(server):
    _console, base = server
    with pytest.raises(urllib.error.HTTPError) as info:
        _get(f"{base}/api/nope")
    assert info.value.code == 404
