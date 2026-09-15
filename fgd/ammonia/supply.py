"""氨站供氨。

喷氨之前必须先建立稳定的供氨压力。压力没建立就投喷枪会空喷，氨量不足导致
催化剂表面积盐，脱硝效率下降且很难在运行中恢复。
"""

from __future__ import annotations

import threading

from ..config import AmmoniaLimits
from ..eventbus import Event, EventBus
from ..errors import InterlockError


class AmmoniaSupply:
    """氨站供氨压力与就绪状态。"""

    def __init__(self, bus: EventBus, limits: AmmoniaLimits) -> None:
        self._bus = bus
        self._limits = limits
        self._pressure_kpa = 0.0
        self._established = False
        self._tripped = False
        self._lock = threading.RLock()

    @property
    def pressure_kpa(self) -> float:
        return self._pressure_kpa

    def establish(self, pressure_kpa: float) -> float:
        """建立供氨压力。"""

        if self._tripped:
            raise InterlockError("氨站联锁未复位，无法建立供氨", component="ammonia")
        if pressure_kpa < self._limits.supply_pressure_kpa_min:
            raise InterlockError(
                f"供氨压力 {pressure_kpa:.3f} kPa 低于下限 "
                f"{self._limits.supply_pressure_kpa_min:.3f} kPa",
                component="ammonia",
            )
        with self._lock:
            self._pressure_kpa = float(pressure_kpa)
            self._established = True
        self._bus.publish(
            Event.of("ammonia", "ammonia", "supply_established", pressure_kpa=pressure_kpa)
        )
        return self._pressure_kpa

    def is_established(self) -> bool:
        with self._lock:
            return bool(self._established and not self._tripped)

    def require_established(self) -> None:
        """下游动喷枪之前的前置检查。"""

        if not self.is_established():
            raise InterlockError("供氨尚未建立，禁止投运喷枪", component="ammonia")

    def trip(self, reason: str) -> None:
        """安全联锁切断供氨。"""

        with self._lock:
            self._tripped = True
            self._established = False
        self._bus.publish(
            Event.of(
                "ammonia",
                "ammonia",
                "supply_tripped",
                subject="ammonia_supply",
                severity="alarm",
                reason=reason,
            )
        )

    def reset(self) -> None:
        """联锁复位，允许重新建立供氨。"""

        with self._lock:
            self._tripped = False
        self._bus.publish(Event.of("ammonia", "ammonia", "supply_reset", subject="ammonia_supply"))

    def shutoff(self, reason: str) -> None:
        """停运顺序里的正常切断：不是联锁跳闸，但要断氨并留痕。"""

        with self._lock:
            self._established = False
            self._pressure_kpa = 0.0
            self._tripped = False
        self._bus.publish(
            Event.of(
                "ammonia",
                "ammonia",
                "supply_shutoff",
                subject="ammonia_supply",
                severity="notice",
                reason=reason,
            )
        )

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "pressure_kpa": round(self._pressure_kpa, 4),
                "established": self._established,
                "tripped": self._tripped,
                "min_pressure_kpa": self._limits.supply_pressure_kpa_min,
            }
