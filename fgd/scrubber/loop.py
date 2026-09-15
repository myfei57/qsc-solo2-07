"""浆液循环。

浆液循环泵的启动有两个硬前置：氧化风机已运行（否则亚硫酸盐在浆液里堆积），
引风机负压已落盘（否则控制器重启后会按未知负压继续循环，烟气倒灌进吸收
塔）。两个检查分别跨氧化和引风组件，顺序错位都会在审计里留下明确记录。
"""

from __future__ import annotations

import threading

from ..eventbus import Event, EventBus
from ..errors import InterlockError
from ..fan.state import FanUnit
from ..oxid.blower import OxidationBlower


class SlurryLoop:
    """浆液循环泵组。"""

    def __init__(self, bus: EventBus, flow_m3_h: float) -> None:
        if flow_m3_h <= 0:
            raise ValueError("循环流量必须为正")
        self._bus = bus
        self._flow_m3_h = float(flow_m3_h)
        self._running = False
        self._flush_count = 0
        self._lock = threading.RLock()

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    @property
    def flow_m3_h(self) -> float:
        with self._lock:
            return self._flow_m3_h if self._running else 0.0

    def start(self, oxid: OxidationBlower, fan: FanUnit) -> float:
        """在氧化风机运行、引风机负压落盘之后启动循环泵。"""

        fan.require_durable()
        oxid.require_running()
        with self._lock:
            self._running = True
            flow = self._flow_m3_h
        self._bus.publish(
            Event.of(
                "scrubber",
                "scrubber",
                "slurry_loop_started",
                subject="slurry_loop",
                flow_m3_h=flow,
                fan_phase=fan.phase.value,
            )
        )
        return flow

    def require_running(self) -> None:
        if not self.running:
            raise InterlockError("浆液循环未运行", component="scrubber")

    def flush(self, duration_s: float) -> float:
        """停运冲洗：泵保持运行把残余浆液带走。"""

        if duration_s <= 0:
            raise ValueError("冲洗时长必须为正")
        self.require_running()
        with self._lock:
            self._flush_count += 1
        self._bus.publish(
            Event.of(
                "scrubber",
                "scrubber",
                "slurry_flush",
                subject="slurry_loop",
                duration_s=duration_s,
            )
        )
        return duration_s

    def stop(self) -> None:
        if self.running:
            with self._lock:
                self._running = False
            self._bus.publish(
                Event.of(
                    "scrubber", "scrubber", "slurry_loop_stopped", subject="slurry_loop"
                )
            )

    def stage_check(self, oxid: OxidationBlower, fan: FanUnit) -> dict:
        """启动前的条件体检，返回每一项是否满足。"""

        return {
            "fan_durable": fan.is_durable(),
            "fan_negative_pressure_ok": fan.negative_pressure_ok(),
            "oxid_running": oxid.is_running(),
        }

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "running": self._running,
                "flow_m3_h": round(self._flow_m3_h, 2),
                "flush_count": self._flush_count,
            }
