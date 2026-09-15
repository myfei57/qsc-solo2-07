"""浆液循环的 pH 调节。

循环泵每个控制周期读取浆液 pH：闩锁置位时不补浆（保护动作），闩锁复位后按
偏差补浆。补浆动作交给石灰浆系统执行，循环侧只决定"该不该补、补多少"。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import ScrubberLimits
from ..lime.feed import LimeFeed
from .latch import PhLatch


@dataclass
class CirculationPump:
    """循环泵的 pH 调节逻辑。"""

    limits: ScrubberLimits
    latch: PhLatch
    feed: LimeFeed
    flow_m3_h: float

    def alkali_demand_pct(self, ph: float) -> float:
        """按 pH 偏差算补浆需求量，未偏低于目标时返回 0。"""

        if ph >= self.limits.ph_target:
            return 0.0
        deficit = self.limits.ph_target - ph
        return round(min(3.0, deficit * 1.8), 4)

    def step(self, ph: float) -> dict:
        """推进一个 pH 调节周期，返回本周期动作摘要。"""

        latched = self.latch.observe(ph)
        demand = self.alkali_demand_pct(ph)
        fed = 0.0
        if demand > 0:
            fed = self.feed.feed(blocked=self.latch.is_blocking(), demand_pct=demand)
        return {
            "ph": round(ph, 4),
            "latched": latched,
            "demand_pct": demand,
            "fed_pct": round(fed, 4),
            "flow_m3_h": self.flow_m3_h,
            "density_pct": round(self.feed.slurry.density_pct, 3),
        }
