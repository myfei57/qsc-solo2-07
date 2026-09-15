"""催化剂状态。

催化剂效率取决于喷氨是否正常：带氨喷入时效率维持在设计值，空喷会留下
沉积并压低效率。把这一状态放在独立组件里，脱硝回路与排放判定都能读取。
"""

from __future__ import annotations

import threading


class Catalyst:
    """脱硝催化剂层。"""

    def __init__(self, design_efficiency: float = 0.90, dry_spray_penalty: float = 0.035) -> None:
        if not 0.0 < design_efficiency <= 1.0:
            raise ValueError("设计效率必须在 (0, 1] 区间")
        self._design = float(design_efficiency)
        self._penalty = float(dry_spray_penalty)
        self._dry_events = 0
        self._lock = threading.RLock()

    def note_spray(self, ammonia_ready: bool) -> float:
        """记录一次喷氨；空喷累加沉积惩罚，返回当前效率。"""

        if not ammonia_ready:
            with self._lock:
                self._dry_events += 1
        return self.efficiency()

    def efficiency(self) -> float:
        with self._lock:
            value = self._design - self._penalty * self._dry_events
        return max(0.0, round(value, 4))

    def regenerate(self) -> float:
        """检修再生：清掉沉积，效率回到设计值。"""

        with self._lock:
            self._dry_events = 0
        return self.efficiency()

    def dry_events(self) -> int:
        with self._lock:
            return self._dry_events

    def snapshot(self) -> dict:
        return {
            "design_efficiency": self._design,
            "efficiency": self.efficiency(),
            "dry_spray_events": self.dry_events(),
        }
