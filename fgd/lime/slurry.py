"""浆液池。

浆液池同时承载 pH、密度和亚硫酸盐三个状态：补浆提 pH 与密度，氧化风把
亚硫酸盐转成硫酸盐，停运时排空浆液。这三个动作分别来自供浆、氧化风机和
停运顺序，池子本身只维护状态与守恒关系。
"""

from __future__ import annotations

import threading


class Slurry:
    """脱硫浆液池。"""

    SULFITE_PER_LOAD = 0.9
    OXIDATION_PER_FLOW = 0.0016

    def __init__(self, ph: float = 5.80, density_pct: float = 15.0) -> None:
        self._ph = float(ph)
        self._density = float(density_pct)
        self._sulfite_ppm = 0.0
        self._fed_pct = 0.0
        self._drained = False
        self._lock = threading.RLock()

    @property
    def ph(self) -> float:
        with self._lock:
            return self._ph

    @property
    def density_pct(self) -> float:
        with self._lock:
            return self._density

    @property
    def sulfite_ppm(self) -> float:
        with self._lock:
            return self._sulfite_ppm

    def add_lime(self, feed_pct: float, alkali_strength: float = 0.11) -> float:
        """补浆：按加入量与碱度抬升 pH 与密度。"""

        if feed_pct < 0:
            raise ValueError("补浆量不能为负")
        with self._lock:
            self._fed_pct += float(feed_pct)
            self._ph = min(7.0, self._ph + alkali_strength * float(feed_pct))
            self._density = min(30.0, self._density + 0.35 * float(feed_pct))
            return self._ph

    def absorb_so2(self, load: float, removal: float) -> float:
        """按负荷与脱硫效率消耗碱度，返回残余 SO2 指数。"""

        with self._lock:
            consumed = load * (1.0 - removal)
            self._ph = max(3.5, self._ph - 0.02 * load)
            self._density = max(0.0, self._density - 0.004 * load)
            return consumed

    def oxidize(self, air_flow_m3_h: float, load: float) -> float:
        """氧化风把亚硫酸盐转成硫酸盐，返回当前亚硫酸盐浓度。"""

        with self._lock:
            self._sulfite_ppm += self.SULFITE_PER_LOAD * load
            self._sulfite_ppm = max(
                0.0, self._sulfite_ppm - self.OXIDATION_PER_FLOW * float(air_flow_m3_h)
            )
            return self._sulfite_ppm

    def drain(self) -> float:
        """停运排空：把浆液密度与亚硫酸盐清零，返回排空前的密度。"""

        with self._lock:
            density = self._density
            self._density = 0.0
            self._sulfite_ppm = 0.0
            self._drained = True
        return density

    def depleted(self, minimum_pct: float) -> bool:
        with self._lock:
            return self._density <= minimum_pct

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "ph": round(self._ph, 4),
                "density_pct": round(self._density, 3),
                "sulfite_ppm": round(self._sulfite_ppm, 3),
                "fed_pct": round(self._fed_pct, 3),
                "drained": self._drained,
            }
