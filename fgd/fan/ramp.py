"""负压升／降过程规划。

引风机从静止升到工作负压需要按速率分段推进，停机时也要按同样方式退出。
把过程抽象成可枚举的分段，控制器在每段之间可以落盘中间状态，验证环境里
也能在不真实等待的前提下走完整条曲线。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..clock import Clock


@dataclass(frozen=True)
class RampPlan:
    """一段线性负压曲线。"""

    start_kpa: float
    target_kpa: float
    rate_kpa_per_s: float

    def __post_init__(self) -> None:
        if self.rate_kpa_per_s <= 0:
            raise ValueError("升压速率必须为正")

    @property
    def duration_s(self) -> float:
        return abs(self.target_kpa - self.start_kpa) / self.rate_kpa_per_s

    def value_at(self, elapsed_s: float) -> float:
        """返回曲线上 ``elapsed_s`` 时刻的负压值。"""

        if elapsed_s <= 0:
            return self.start_kpa
        if elapsed_s >= self.duration_s:
            return self.target_kpa
        progress = elapsed_s / self.duration_s
        return self.start_kpa + (self.target_kpa - self.start_kpa) * progress

    def steps(self, step_s: float) -> list[float]:
        """把曲线切成不超过 ``step_s`` 的采样时刻（含 0 与终点）。"""

        if step_s <= 0:
            raise ValueError("分段步长必须为正")
        marks = [0.0]
        elapsed = 0.0
        while elapsed < self.duration_s:
            elapsed = min(elapsed + step_s, self.duration_s)
            marks.append(elapsed)
        return marks

    def describe(self) -> dict:
        return {
            "start_kpa": round(self.start_kpa, 4),
            "target_kpa": round(self.target_kpa, 4),
            "rate_kpa_per_s": self.rate_kpa_per_s,
            "duration_s": round(self.duration_s, 3),
        }


def plan_ramp(start_kpa: float, target_kpa: float, rate_kpa_per_s: float) -> RampPlan:
    """按当前值与目标值规划负压曲线。"""

    return RampPlan(
        start_kpa=float(start_kpa),
        target_kpa=float(target_kpa),
        rate_kpa_per_s=float(rate_kpa_per_s),
    )


def execute_ramp(
    plan: RampPlan,
    clock: Clock,
    step_s: float,
    on_step: object = None,
) -> float:
    """按分段推进曲线，返回终点值。

    ``on_step`` 是可选回调，签名为 ``(elapsed_s, value)``；引风机用它记录
    中间轨迹，停机时用它确认负压确实在衰减。
    """

    marks = plan.steps(step_s)
    previous = marks[0]
    for mark in marks[1:]:
        clock.sleep(mark - previous)
        previous = mark
        if callable(on_step):
            on_step(mark, plan.value_at(mark))
    return plan.value_at(marks[-1])


@dataclass
class RampTrace:
    """负压曲线执行轨迹。"""

    points: list[tuple[float, float]] = field(default_factory=list)

    def note(self, elapsed_s: float, value: float) -> None:
        self.points.append((round(elapsed_s, 3), round(value, 4)))

    def as_dict(self) -> dict:
        return {"points": [list(point) for point in self.points]}
