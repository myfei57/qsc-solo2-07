"""振打清灰周期。

电场极板积灰到一定程度必须振打清灰，振打期间该电场短暂退出工作。周期按
配置推进，振打次数与最近一次振打时刻写入审计，供巡检对照出口含尘量。
"""

from __future__ import annotations

import threading

from ..clock import Clock
from ..config import EspLimits
from ..eventbus import Event, EventBus


class RappingCycle:
    """按固定周期触发的振打。"""

    def __init__(self, clock: Clock, bus: EventBus, limits: EspLimits) -> None:
        self._clock = clock
        self._bus = bus
        self._interval = limits.rapping_interval_s
        self._last_rap = clock.now()
        self._count = 0
        self._lock = threading.RLock()

    def next_due(self) -> float:
        """返回下一次振打的挂钟时刻。"""

        with self._lock:
            return self._last_rap + self._interval

    def due(self) -> bool:
        return self._clock.now() >= self.next_due()

    def run(self, field_kv: float) -> dict:
        """执行一次振打并返回本次事件的载荷。"""

        with self._lock:
            self._count += 1
            self._last_rap = self._clock.now()
            count = self._count
        payload = {"count": count, "field_kv": round(field_kv, 3)}
        self._bus.publish(
            Event.of("esp", "esp", "rapping_cycle", subject="esp_rapping", **payload)
        )
        return payload

    def run_if_due(self, field_kv: float) -> dict | None:
        """到点才振打，未到点返回 ``None``。"""

        if self.due():
            return self.run(field_kv)
        return None

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "count": self._count,
                "last_rap": round(self._last_rap, 3),
                "next_due": round(self.next_due(), 3),
                "interval_s": self._interval,
            }
