"""氨站安全联锁。

安全联锁按秒级周期巡检：一旦氨浓度或供氨压力越界，把喷氨率压到安全上限
以下，并优先于 NOx 自动调节提交到阀门。联锁不复位时自动调节只能读到被
压低的提交值。
"""

from __future__ import annotations

import threading

from ..config import AmmoniaLimits
from ..eventbus import Event, EventBus
from .supply import AmmoniaSupply
from .valve import AmmoniaValve, ValveOutcome


class AmmoniaSafety:
    """氨站安全巡检。"""

    def __init__(
        self,
        bus: EventBus,
        limits: AmmoniaLimits,
        valve: AmmoniaValve,
        supply: AmmoniaSupply,
    ) -> None:
        self._bus = bus
        self._limits = limits
        self._valve = valve
        self._supply = supply
        self._tripped = False
        self._checks = 0
        self._last_concentration = 0.0
        self._lock = threading.RLock()

    @property
    def tripped(self) -> bool:
        return self._tripped

    def safe_cap(self) -> float:
        """联锁允许的最大喷氨率。"""

        return self._limits.max_rate_kg_h - self._limits.safety_margin_kg_h

    def check(self, concentration_ppm: float, pressure_kpa: float, limit_ppm: float = 25.0) -> ValveOutcome | None:
        """执行一次安全巡检，越界时抢占阀门。"""

        with self._lock:
            self._checks += 1
            self._last_concentration = float(concentration_ppm)
        low_pressure = pressure_kpa < self._limits.supply_pressure_kpa_min
        over_limit = concentration_ppm > limit_ppm
        if not low_pressure and not over_limit:
            if self._tripped:
                with self._lock:
                    self._tripped = False
                self._supply.reset()
                # 巡检恢复正常：把供氨重新建立起来，喷枪侧才有继续投运的条件。
                self._supply.establish(pressure_kpa)
                self._bus.publish(
                    Event.of("ammonia", "ammonia", "safety_recovered", subject="ammonia_safety")
                )
            return None
        with self._lock:
            self._tripped = True
        reason = "concentration" if over_limit else "pressure"
        self._supply.trip(reason)
        self._bus.publish(
            Event.of(
                "ammonia",
                "ammonia",
                "safety_trip",
                subject="ammonia_safety",
                severity="alarm",
                reason=reason,
                concentration_ppm=round(concentration_ppm, 3),
                pressure_kpa=round(pressure_kpa, 4),
            )
        )
        self._valve.request("safety", self.safe_cap(), AmmoniaValve.SAFETY_PRIORITY)
        return self._valve.resolve()

    def checks(self) -> int:
        with self._lock:
            return self._checks

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "tripped": self._tripped,
                "checks": self._checks,
                "last_concentration_ppm": round(self._last_concentration, 3),
                "safe_cap_kg_h": round(self.safe_cap(), 3),
            }
