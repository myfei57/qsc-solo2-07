"""浆液 pH 闩锁。

pH 低于下限时置位闩锁并停浆，属于保护动作；pH 回升到恢复值以上时闩锁必须
自动复位，否则供浆会一直被挡住，出口 SO2 会持续往上爬。闩锁带迟滞：置位点
与复位点不是同一个值，避免在阈值附近反复抖动。
"""

from __future__ import annotations

import threading

from ..config import ScrubberLimits
from ..eventbus import Event, EventBus


class PhLatch:
    """pH 联锁闩锁。"""

    def __init__(self, bus: EventBus, limits: ScrubberLimits) -> None:
        self._bus = bus
        self._limits = limits
        self._latched = False
        self._resets = 0
        self._sets = 0
        self._last_ph: float | None = None
        self._lock = threading.RLock()

    @property
    def latched(self) -> bool:
        with self._lock:
            return self._latched

    def is_blocking(self) -> bool:
        """闩锁置位期间供浆被禁止。"""

        return self.latched

    def observe(self, ph: float) -> bool:
        """按当前 pH 更新闩锁，返回更新后的置位状态。"""

        with self._lock:
            self._last_ph = float(ph)
            if not self._latched and self._limits.latch_should_set(ph):
                self._latched = True
                self._sets += 1
                changed = True
            elif self._latched and self._limits.latch_should_reset(ph):
                self._latched = False
                self._resets += 1
                changed = True
            else:
                changed = False
            latched = self._latched
        if changed:
            self._bus.publish(
                Event.of(
                    "scrubber",
                    "scrubber",
                    "ph_latch_set" if latched else "ph_latch_reset",
                    subject="ph_latch",
                    severity="warning" if latched else "notice",
                    ph=round(ph, 4),
                    threshold=round(
                        self._limits.ph_low if latched else self._limits.ph_recover, 4
                    ),
                )
            )
        return latched

    def reset(self) -> bool:
        """人工复位（检修用），返回复位前是否处于置位状态。"""

        with self._lock:
            was = self._latched
            self._latched = False
            if was:
                self._resets += 1
        if was:
            self._bus.publish(
                Event.of(
                    "scrubber",
                    "scrubber",
                    "ph_latch_manual_reset",
                    subject="ph_latch",
                    severity="notice",
                )
            )
        return was

    def counts(self) -> dict:
        with self._lock:
            return {"sets": self._sets, "resets": self._resets}

    def snapshot(self) -> dict:
        counts = self.counts()
        with self._lock:
            return {
                "latched": self._latched,
                "last_ph": self._last_ph,
                "ph_low": self._limits.ph_low,
                "ph_recover": self._limits.ph_recover,
                "sets": counts["sets"],
                "resets": counts["resets"],
            }
