"""负压调节回路。

锅炉侧负压回路和脱硫侧压降回路使用同一个 PI 结构，但目标与优先级不同：
锅炉侧要优先保住炉膛负压（防正压喷灰），脱硫侧只在锅炉侧稳定后微调系统
压降（防浆液泵汽蚀）。两者把请求交给同一个仲裁器，避免各自直接写转速。
"""

from __future__ import annotations

from dataclasses import dataclass

from .speed import SpeedArbiter, SpeedRequest


@dataclass
class PressureController:
    """带积分限幅的 PI 控制器。"""

    kp: float
    ki: float
    output_min: float
    output_max: float
    _integral: float = 0.0

    def update(self, measured: float, target: float, dt: float) -> float:
        """按偏差推进一个控制周期，返回输出量。"""

        if dt <= 0:
            raise ValueError("控制周期必须为正")
        error = target - measured
        self._integral += error * dt
        raw = self.kp * error + self.ki * self._integral
        clamped = max(self.output_min, min(self.output_max, raw))
        if clamped != raw:
            # 输出饱和时回退积分，避免退饱和过程中长时间跟不上。
            self._integral -= error * dt
        return clamped

    def reset(self) -> None:
        self._integral = 0.0

    @property
    def integral(self) -> float:
        return self._integral


class _DraftLoop:
    """两个负压回路共用的骨架。"""

    LOOP_NAME = ""
    PRIORITY = 0

    def __init__(self, arbiter: SpeedArbiter, controller: PressureController, base_rpm: float) -> None:
        self._arbiter = arbiter
        self._controller = controller
        self._base_rpm = float(base_rpm)
        self._last_measured: float | None = None

    @property
    def loop_name(self) -> str:
        return self.LOOP_NAME

    def step(self, measured_kpa: float, target_kpa: float, dt: float) -> SpeedRequest:
        """推进一个控制周期并把请求提交给仲裁器。"""

        bias = self._controller.update(measured_kpa, target_kpa, dt)
        rpm = self._arbiter.clamp(self._base_rpm + bias)
        self._last_measured = measured_kpa
        return self._arbiter.request(
            self.LOOP_NAME, rpm, self.PRIORITY, reason=f"deviation={target_kpa - measured_kpa:+.4f}"
        )

    def reset(self) -> None:
        self._controller.reset()
        self._last_measured = None

    def snapshot(self) -> dict:
        return {
            "loop": self.loop_name,
            "priority": self.PRIORITY,
            "base_rpm": self._base_rpm,
            "integral": round(self._controller.integral, 4),
            "last_measured_kpa": self._last_measured,
        }


class BoilerDraftLoop(_DraftLoop):
    """锅炉负压回路：优先级最高，先保住炉膛负压。"""

    LOOP_NAME = "boiler_draft"
    PRIORITY = 60


class FgdDraftLoop(_DraftLoop):
    """脱硫压降回路：优先级低于锅炉侧，只做补偿。"""

    LOOP_NAME = "fgd_pressure"
    PRIORITY = 40
