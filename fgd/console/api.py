"""控制台 JSON 接口。

接口只做参数校验收敛与转发，业务判断全部留在组件里：状态来自 ``Platform``
的状态快照，趋势走趋势缓冲，启停走顺序执行器。控制台因此不会绕开联锁。
"""

from __future__ import annotations

from ..app import CycleInputs, Platform
from ..errors import FgdError

MAX_TREND_WINDOW_S = 86_400.0
MAX_AUDIT_LIMIT = 500


class ConsoleAPI:
    """把 HTTP 请求转成平台动作。"""

    def __init__(self, platform: Platform) -> None:
        self.platform = platform

    def status(self) -> dict:
        payload = self.platform.status()
        payload["topology"] = self.platform.topology_summary()
        payload["namespaces"] = self.platform.namespaces.describe()
        return payload

    def trend(self, series: str | None = None, window_s: float = 600.0) -> dict:
        """返回趋势窗口：采样点、均值与最新值。"""

        window = max(1.0, min(MAX_TREND_WINDOW_S, float(window_s)))
        names = [series] if series else self.platform.trend.series_names()
        unknown = [name for name in names if name not in self.platform.trend.series_names()]
        if unknown:
            raise ValueError(f"未知的趋势分量: {', '.join(unknown)}")
        end = self.platform.clock.now()
        start = end - window
        result: dict[str, dict] = {}
        for name in names:
            filled = self.platform.trend.fill_gaps(name)
            samples = self.platform.trend.window(name, start, end)
            latest = self.platform.trend.latest(name)
            result[name] = {
                "filled_points": filled,
                "samples": [sample.as_dict() for sample in samples],
                "average": round(self.platform.trend.average(name, start, end), 4),
                "latest": latest.as_dict() if latest else None,
            }
        return {"window_s": window, "start": round(start, 3), "end": round(end, 3), "series": result}

    def alarms(self) -> dict:
        alarms = self.platform.audit.alarms()
        return {"count": len(alarms), "alarms": alarms}

    def audit(self, limit: int = 100) -> dict:
        bounded = max(1, min(MAX_AUDIT_LIMIT, int(limit)))
        return {
            "entries": self.platform.audit.entries(bounded),
            "recent": self.platform.audit.tail(bounded // 4 + 1),
        }

    def readiness(self) -> dict:
        """启动前体检：把每个前置条件摊开给控制台。"""

        platform = self.platform
        return {
            "slurry_loop": platform.loop.stage_check(platform.blower, platform.fan),
            "fan_pressure_kpa": round(platform.fan.pressure_kpa, 4),
            "fan_durable": platform.fan.is_durable(),
            "esp_energized": platform.esp.is_energized(),
            "supply_established": platform.supply.is_established(),
        }

    def journal(self, segment: int | None = None, limit: int = 200) -> dict:
        """读落盘日志；给段号时只读该段。"""

        if segment is None:
            return {"segment": None, "entries": self.platform.audit.replay_from_journal(limit)}
        return {"segment": segment, "entries": self.platform.audit.replay_segment(segment)}

    def start(self) -> dict:
        try:
            return {"ok": True, "report": self.platform.start()}
        except FgdError as error:
            return {"ok": False, "error": self.platform.note_error(error)}

    def stop(self) -> dict:
        try:
            return {"ok": True, "report": self.platform.stop()}
        except FgdError as error:
            return {"ok": False, "error": self.platform.note_error(error)}

    def step(self, payload: dict) -> dict:
        """推进一个控制周期；缺省工况按 85% 负荷、NOx 目标 42 mg/m3。"""

        inputs = CycleInputs(
            load=float(payload.get("load", 0.85)),
            nox_target=float(payload.get("nox_target", 42.0)),
            ammonia_concentration_ppm=float(payload.get("ammonia_concentration_ppm", 4.0)),
            dt=float(payload.get("dt", 1.0)),
        )
        try:
            return {"ok": True, "cycle": self.platform.cycle(inputs)}
        except FgdError as error:
            return {"ok": False, "error": self.platform.note_error(error)}

    def calibrate(self, payload: dict) -> dict:
        """重新标定分析仪。"""

        offset = float(payload.get("offset_mg_m3", 0.0))
        return {"ok": True, "calibration": self.platform.calibration_probe(offset)}

    def regenerate_catalyst(self, payload: dict) -> dict:
        """催化剂检修再生。"""

        reason = str(payload.get("reason", "控制台触发的检修再生"))
        return {"ok": True, "catalyst": self.platform.regenerate_catalyst(reason)}
