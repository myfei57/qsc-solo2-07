"""脱硝喷枪。

喷枪只有在供氨建立之后才允许投运；投运前必须先确认氨站压力到位，否则喷枪
空喷会在催化剂表面留下沉积，且这类沉积在运行中很难消除。
"""

from __future__ import annotations

import threading

from ..ammonia.supply import AmmoniaSupply
from ..eventbus import Event, EventBus
from ..errors import SequenceError
from .catalyst import Catalyst


class AmmoniaInjector:
    """脱硝喷氨喷枪。"""

    def __init__(self, bus: EventBus, catalyst: Catalyst, min_rate_kg_h: float) -> None:
        self._bus = bus
        self._catalyst = catalyst
        self._min_rate = float(min_rate_kg_h)
        self._running = False
        self._rate_kg_h = 0.0
        self._dry_spray_events = 0
        self._lock = threading.RLock()

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    @property
    def rate_kg_h(self) -> float:
        with self._lock:
            return self._rate_kg_h

    def start_injection(self, supply: AmmoniaSupply) -> float:
        """在供氨建立之后投运喷枪，返回初始喷氨率。"""

        supply.require_established()
        with self._lock:
            self._running = True
            self._rate_kg_h = self._min_rate
        self._catalyst.note_spray(ammonia_ready=True)
        self._bus.publish(
            Event.of(
                "denox",
                "denox",
                "injector_started",
                subject="denox_injector",
                rate_kg_h=self._rate_kg_h,
                supply_pressure_kpa=round(supply.pressure_kpa, 4),
            )
        )
        return self._rate_kg_h

    def set_rate(self, rate_kg_h: float) -> float:
        """按喷嘴执行器能力写入实际喷氨率。"""

        if not self.running:
            raise SequenceError("喷枪未投运，无法调整喷氨率", component="denox")
        with self._lock:
            self._rate_kg_h = max(self._min_rate, float(rate_kg_h))
            rate = self._rate_kg_h
        self._catalyst.note_spray(ammonia_ready=True)
        return rate

    def stop(self) -> None:
        with self._lock:
            self._running = False
            self._rate_kg_h = 0.0
        self._bus.publish(
            Event.of("denox", "denox", "injector_stopped", subject="denox_injector")
        )

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "running": self._running,
                "rate_kg_h": round(self._rate_kg_h, 3),
                "dry_spray_events": self._dry_spray_events,
                "min_rate_kg_h": self._min_rate,
            }
