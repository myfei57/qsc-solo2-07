"""石灰浆供浆。

供浆受 pH 闩锁约束：闩锁置位期间禁止补浆，闩锁恢复后必须立即恢复供浆。
供浆动作只改浆液池状态，pH 联锁的判定留在脱硫塔侧，避免两个组件各自维护
一份 pH 阈值。
"""

from __future__ import annotations

import threading

from ..config import LimeLimits
from ..eventbus import Event, EventBus
from .slurry import Slurry


class LimeFeed:
    """石灰浆供浆阀。"""

    def __init__(self, bus: EventBus, slurry: Slurry, limits: LimeLimits) -> None:
        self._bus = bus
        self._slurry = slurry
        self._limits = limits
        self._total_pct = 0.0
        self._blocked_count = 0
        self._lock = threading.RLock()

    @property
    def total_pct(self) -> float:
        with self._lock:
            return self._total_pct

    @property
    def slurry(self) -> Slurry:
        """供浆阀后面的浆液池，供循环调节读取密度与 pH。"""

        return self._slurry

    def density_in_range(self) -> bool:
        """浆液密度是否仍在工艺区间内。"""

        density = self._slurry.density_pct
        if self._slurry.depleted(self._limits.min_density_pct):
            return False
        return density <= self._limits.max_density_pct

    def feed(self, blocked: bool, demand_pct: float | None = None) -> float:
        """按需补浆；``blocked`` 为真时只记录被拒绝，不动作。"""

        step = self._limits.feed_step_pct if demand_pct is None else float(demand_pct)
        if step <= 0:
            raise ValueError("补浆步长必须为正")
        if blocked:
            with self._lock:
                self._blocked_count += 1
            self._bus.publish(
                Event.of(
                    "lime",
                    "lime",
                    "feed_blocked",
                    subject="lime_feed",
                    severity="warning",
                    step_pct=step,
                )
            )
            return 0.0
        ph_after = self._slurry.add_lime(step)
        with self._lock:
            self._total_pct += step
        self._bus.publish(
            Event.of(
                "lime",
                "lime",
                "lime_fed",
                subject="lime_feed",
                step_pct=step,
                ph_after=round(ph_after, 4),
                density_pct=round(self._slurry.density_pct, 3),
            )
        )
        return step

    def blocked_count(self) -> int:
        with self._lock:
            return self._blocked_count

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "total_pct": round(self._total_pct, 3),
                "blocked_count": self._blocked_count,
                "density_in_range": self.density_in_range(),
                "slurry": self._slurry.snapshot(),
                "limits": {
                    "min_density_pct": self._limits.min_density_pct,
                    "max_density_pct": self._limits.max_density_pct,
                    "feed_step_pct": self._limits.feed_step_pct,
                },
            }
