"""烟气分析仪标定基线。

分析仪重新校验后偏移量会变，排放判定必须使用最新标定。基线带标定时刻，
读取时可以算出生效时长：过期基线按默认偏移量处理并给出告警，而不是继续用
一份来路不明的旧值判定排放。
"""

from __future__ import annotations

import threading


class AnalyzerOffset:
    """当前有效的分析仪偏移量。"""

    def __init__(self, default_offset_mg_m3: float, calibrated_at: float) -> None:
        self._offset = float(default_offset_mg_m3)
        self._default = float(default_offset_mg_m3)
        self._calibrated_at = float(calibrated_at)
        self._epoch = 0
        self._lock = threading.RLock()

    @property
    def epoch(self) -> int:
        """标定世代：每次重新校验自增，用于确认业务读到的是新基线。"""

        with self._lock:
            return self._epoch

    def calibrate(self, offset_mg_m3: float, at: float) -> int:
        """写入新标定，返回新的标定世代。"""

        with self._lock:
            self._offset = float(offset_mg_m3)
            self._calibrated_at = float(at)
            self._epoch += 1
            return self._epoch

    def current(self) -> float:
        with self._lock:
            return self._offset

    def calibrated_at(self) -> float:
        with self._lock:
            return self._calibrated_at

    def age_s(self, now: float) -> float:
        """返回标定生效时长。"""

        with self._lock:
            return max(0.0, float(now) - self._calibrated_at)

    def reset(self) -> float:
        """回到出厂默认偏移量，返回复位后的值。"""

        with self._lock:
            self._offset = self._default
            self._epoch += 1
            return self._offset

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "offset_mg_m3": round(self._offset, 4),
                "default_offset_mg_m3": round(self._default, 4),
                "calibrated_at": round(self._calibrated_at, 3),
                "epoch": self._epoch,
            }
