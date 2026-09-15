"""氧化风机本体。

氧化风机把亚硫酸盐氧化成硫酸盐，是石膏品质的前提。风机未运行时浆液里的
亚硫酸盐会持续累积，脱水困难；因此浆液循环泵与氧化风机之间是硬性先后关系。
"""

from __future__ import annotations

import threading

from ..eventbus import Event, EventBus
from ..errors import InterlockError


class OxidationBlower:
    """氧化风机。"""

    def __init__(self, bus: EventBus, rated_flow_m3_h: float) -> None:
        if rated_flow_m3_h <= 0:
            raise ValueError("氧化风机额定风量必须为正")
        self._bus = bus
        self._rated = float(rated_flow_m3_h)
        self._running = False
        self._flow_m3_h = 0.0
        self._starts = 0
        self._lock = threading.RLock()

    @property
    def rated_flow_m3_h(self) -> float:
        return self._rated

    @property
    def flow_m3_h(self) -> float:
        with self._lock:
            return self._flow_m3_h

    def start(self, flow_m3_h: float | None = None) -> float:
        """启动风机，默认按额定风量运行。"""

        flow = self._rated if flow_m3_h is None else float(flow_m3_h)
        if flow <= 0 or flow > self._rated:
            raise InterlockError(
                f"氧化风量 {flow:.1f} m3/h 超出 (0, {self._rated:.1f}] 区间",
                component="oxid",
            )
        with self._lock:
            self._running = True
            self._flow_m3_h = flow
            self._starts += 1
        self._bus.publish(Event.of("oxid", "oxid", "blower_started", flow_m3_h=flow))
        return flow

    def is_running(self) -> bool:
        with self._lock:
            return bool(self._running and self._flow_m3_h > 0)

    def require_running(self) -> None:
        """浆液循环之前的前置检查。"""

        if not self.is_running():
            raise InterlockError("氧化风机未运行，禁止启动浆液循环", component="oxid")

    def stop(self) -> None:
        with self._lock:
            self._running = False
            self._flow_m3_h = 0.0
        self._bus.publish(Event.of("oxid", "oxid", "blower_stopped", subject="oxid_blower"))

    def starts(self) -> int:
        with self._lock:
            return self._starts

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "running": self._running,
                "flow_m3_h": round(self._flow_m3_h, 2),
                "rated_flow_m3_h": self._rated,
                "starts": self._starts,
            }
