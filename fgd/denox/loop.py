"""NOx 自动调节回路。

按出口 NOx 偏差算喷氨率，通过喷氨阀仲裁器提交开度。回路本身不直接写阀门，
安全联锁与自动调节因此不会互相覆盖：安全联锁优先级更高，自动调节只能读到
被压低后的提交值。
"""

from __future__ import annotations

from ..ammonia.valve import AmmoniaValve, ValveOutcome
from ..fan.draft import PressureController


class NoxLoop:
    """出口 NOx 闭环。"""

    WRITER = "nox_loop"

    def __init__(self, valve: AmmoniaValve, base_rate_kg_h: float, kp: float, ki: float) -> None:
        self._valve = valve
        self._base_rate = float(base_rate_kg_h)
        self._controller = PressureController(
            kp=kp,
            ki=ki,
            output_min=-float(base_rate_kg_h),
            output_max=float(valve.snapshot()["max_rate_kg_h"] - base_rate_kg_h),
        )
        self._last_measured: float | None = None

    def step(self, nox_measured: float, nox_target: float, dt: float) -> ValveOutcome:
        """推进一个控制周期：算偏差 → 提交请求 → 结算仲裁。"""

        bias = self._controller.update(nox_measured, nox_target, dt)
        rate = self._valve.clamp(self._base_rate + bias)
        self._valve.request(self.WRITER, rate, AmmoniaValve.NOX_LOOP_PRIORITY)
        self._last_measured = float(nox_measured)
        return self._valve.resolve()

    def reset(self) -> None:
        self._controller.reset()
        self._last_measured = None

    def snapshot(self) -> dict:
        return {
            "writer": self.WRITER,
            "base_rate_kg_h": self._base_rate,
            "integral": round(self._controller.integral, 4),
            "last_measured_nox": self._last_measured,
            "committed_rate_kg_h": round(self._valve.committed_rate(), 3),
        }
