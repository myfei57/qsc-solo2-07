"""烟囱排放判定。

分析仪存在固定偏移量：实测值必须先按"当前有效标定"还原成真实值，再与排放
限值比较。标定基线在运行期会更新（重新校验后），判定环节必须每次读取最新
偏移量，不能把第一次读到的那份缓存下来。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from ..clock import Clock
from ..config import AnalyzerLimits, EmissionLimits
from ..eventbus import Event, EventBus
from ..scrubber.offset import AnalyzerOffset
from ..telemetry import TrendBuffer


@dataclass(frozen=True)
class EmissionReading:
    """一组原始烟气测量值（分析仪未修偏）。"""

    so2_mg_m3: float
    nox_mg_m3: float
    dust_mg_m3: float

    def shifted(self, offset_mg_m3: float) -> "EmissionReading":
        """按偏移量还原真实值。"""

        return EmissionReading(
            so2_mg_m3=self.so2_mg_m3 - offset_mg_m3,
            nox_mg_m3=self.nox_mg_m3 - offset_mg_m3,
            dust_mg_m3=self.dust_mg_m3,
        )

    def as_dict(self) -> dict:
        return {
            "so2_mg_m3": round(self.so2_mg_m3, 3),
            "nox_mg_m3": round(self.nox_mg_m3, 3),
            "dust_mg_m3": round(self.dust_mg_m3, 3),
        }


@dataclass(frozen=True)
class EmissionVerdict:
    """一次排放判定结果。"""

    corrected: EmissionReading
    offset_mg_m3: float
    breach: tuple[str, ...]
    severity: str

    @property
    def compliant(self) -> bool:
        return not self.breach

    def as_dict(self) -> dict:
        return {
            "corrected": self.corrected.as_dict(),
            "offset_mg_m3": round(self.offset_mg_m3, 4),
            "breach": list(self.breach),
            "severity": self.severity,
            "compliant": self.compliant,
        }


class StackEmissionControl:
    """按当前标定基线判定排放并记录趋势。"""

    def __init__(
        self,
        clock: Clock,
        bus: EventBus,
        limits: EmissionLimits,
        analyzer: AnalyzerLimits,
        offset: AnalyzerOffset,
        trend: TrendBuffer,
    ) -> None:
        self._clock = clock
        self._bus = bus
        self._limits = limits
        self._analyzer = analyzer
        self._offset = offset
        self._trend = trend
        self._latest: EmissionVerdict | None = None
        self._breach_count = 0
        self._lock = threading.RLock()

    def current_offset(self) -> float:
        """读取当前有效偏移量；基线过期时按默认值并给出告警。"""

        offset = self._offset.current()
        age = self._offset.age_s(self._clock.now())
        if age > self._analyzer.max_age_s:
            self._bus.publish(
                Event.of(
                    "stack",
                    "stack",
                    "analyzer_offset_stale",
                    subject="analyzer",
                    severity="warning",
                    age_s=round(age, 1),
                )
            )
            return self._analyzer.default_offset_mg_m3
        if abs(offset) > self._analyzer.max_offset_mg_m3:
            self._bus.publish(
                Event.of(
                    "stack",
                    "stack",
                    "analyzer_offset_out_of_range",
                    subject="analyzer",
                    severity="alarm",
                    offset=offset,
                )
            )
            return self._analyzer.default_offset_mg_m3
        return offset

    def judge(self, reading: EmissionReading) -> EmissionVerdict:
        """用当前偏移量判定一组读数。"""

        offset = self.current_offset()
        corrected = reading.shifted(offset)
        breach: list[str] = []
        if corrected.so2_mg_m3 > self._limits.so2_limit_mg_m3:
            breach.append("so2")
        if corrected.nox_mg_m3 > self._limits.nox_limit_mg_m3:
            breach.append("nox")
        if corrected.dust_mg_m3 > self._limits.dust_limit_mg_m3:
            breach.append("dust")
        severity = "alarm" if breach else "info"
        return EmissionVerdict(
            corrected=corrected,
            offset_mg_m3=offset,
            breach=tuple(breach),
            severity=severity,
        )

    def evaluate(self, reading: EmissionReading) -> EmissionVerdict:
        """判定、记趋势并发布结果。"""

        verdict = self.judge(reading)
        moment = self._clock.now()
        self._trend.append("so2", moment, verdict.corrected.so2_mg_m3)
        self._trend.append("nox", moment, verdict.corrected.nox_mg_m3)
        self._trend.append("dust", moment, verdict.corrected.dust_mg_m3)
        with self._lock:
            self._latest = verdict
            if not verdict.compliant:
                self._breach_count += 1
        payload = verdict.as_dict()
        payload.pop("severity", None)
        payload["subject"] = "stack"
        payload["severity"] = verdict.severity
        self._bus.publish(Event.of("stack", "stack", "emission_evaluated", **payload))
        return verdict

    def breach_count(self) -> int:
        with self._lock:
            return self._breach_count

    def latest(self) -> dict:
        with self._lock:
            if self._latest is None:
                return {"status": "no-reading"}
            return self._latest.as_dict()
