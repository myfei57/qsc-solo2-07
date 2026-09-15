"""除尘电场升压。

电场必须先在烟气进入前建立起晕电压：带电粉尘才会被电场捕获。未升压就通烟，
粉尘不带电直接穿过，烟囱出口冒灰且在线数据立刻超标。
"""

from __future__ import annotations

import threading

from ..config import EspLimits
from ..eventbus import Event, EventBus
from ..errors import InterlockError


class EspField:
    """一组除尘电场。"""

    def __init__(self, bus: EventBus, limits: EspLimits) -> None:
        self._bus = bus
        self._limits = limits
        self._voltage_kv = 0.0
        self._energized = False
        self._lock = threading.RLock()

    @property
    def voltage_kv(self) -> float:
        return self._voltage_kv

    def energize(self, voltage_kv: float) -> float:
        """升压到工作电压。"""

        if voltage_kv < self._limits.field_kv_min:
            raise InterlockError(
                f"电场电压 {voltage_kv:.2f} kV 低于起晕电压 "
                f"{self._limits.field_kv_min:.2f} kV",
                component="esp",
            )
        if voltage_kv > self._limits.field_kv_max:
            raise InterlockError(
                f"电场电压 {voltage_kv:.2f} kV 超过上限 {self._limits.field_kv_max:.2f} kV",
                component="esp",
            )
        with self._lock:
            self._voltage_kv = float(voltage_kv)
            self._energized = True
        self._bus.publish(
            Event.of("esp", "esp", "field_energized", voltage_kv=voltage_kv)
        )
        return self._voltage_kv

    def is_energized(self) -> bool:
        with self._lock:
            return bool(self._energized and self._voltage_kv >= self._limits.field_kv_min)

    def require_energized(self) -> None:
        """通烟之前的前置检查。"""

        if not self.is_energized():
            raise InterlockError("除尘电场未升压，禁止通烟", component="esp")

    def de_energize(self) -> None:
        """泄压退电场。"""

        with self._lock:
            self._voltage_kv = 0.0
            self._energized = False
        self._bus.publish(Event.of("esp", "esp", "field_de_energized", subject="esp_field"))

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "voltage_kv": round(self._voltage_kv, 3),
                "energized": self._energized,
                "field_kv_min": self._limits.field_kv_min,
                "field_kv_max": self._limits.field_kv_max,
            }
