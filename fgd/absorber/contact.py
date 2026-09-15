"""吸收塔气液接触。

脱硫效率由两层决定：浆液 pH 决定气液两相能不能把 SO2 吸收下来，循环流量
决定喷淋覆盖率。循环泵停运时按空塔处理，出口 SO2 直接按入口值穿透——这
正是"先停泵、后停烟"会造成短时超标的原因。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AbsorbResult:
    """一次吸收计算的结果。"""

    so2_out_mg_m3: float
    removal: float
    ph: float
    loop_running: bool

    def as_dict(self) -> dict:
        return {
            "so2_out_mg_m3": round(self.so2_out_mg_m3, 3),
            "removal": round(self.removal, 4),
            "ph": round(self.ph, 4),
            "loop_running": self.loop_running,
        }


class AbsorberTower:
    """喷淋吸收塔。"""

    BASE_REMOVAL = 0.9386
    PH_GAIN = 0.0862
    FLOW_GAIN = 0.01
    REMOVAL_CAP = 0.99

    def __init__(
        self,
        design_flow_m3_h: float,
        ph_reference: float = 5.2,
        ph_gain: float | None = None,
        base_removal: float | None = None,
    ) -> None:
        if design_flow_m3_h <= 0:
            raise ValueError("设计循环流量必须为正")
        self._design_flow = float(design_flow_m3_h)
        self._ph_reference = float(ph_reference)
        self._ph_gain = self.PH_GAIN if ph_gain is None else float(ph_gain)
        self._base_removal = (
            self.BASE_REMOVAL if base_removal is None else float(base_removal)
        )

    @property
    def design_flow_m3_h(self) -> float:
        return self._design_flow

    @property
    def ph_reference(self) -> float:
        return self._ph_reference

    def contact_efficiency(self, ph: float, flow_m3_h: float) -> float:
        """按 pH 与循环流量计算脱硫效率。

        pH 是主导项：低于联锁下限后每降 0.1 个 pH，出口 SO2 会明显抬头；
        循环流量只提供少量裕度，泵停运时整条效率归零（空塔穿透）。
        """

        ph_term = self._ph_gain * (ph - self._ph_reference)
        flow_term = min(1.0, flow_m3_h / self._design_flow) * self.FLOW_GAIN
        return max(0.0, min(self.REMOVAL_CAP, self._base_removal + ph_term + flow_term))

    def absorb(
        self, so2_in_mg_m3: float, ph: float, flow_m3_h: float, loop_running: bool
    ) -> AbsorbResult:
        """计算吸收后的出口 SO2。"""

        removal = self.contact_efficiency(ph, flow_m3_h) if loop_running else 0.0
        return AbsorbResult(
            so2_out_mg_m3=so2_in_mg_m3 * (1.0 - removal),
            removal=removal,
            ph=ph,
            loop_running=loop_running,
        )

    def snapshot(self, ph: float, flow_m3_h: float, loop_running: bool) -> dict:
        """控制台展示用的塔况快照。"""

        efficiency = self.contact_efficiency(ph, flow_m3_h) if loop_running else 0.0
        return {
            "design_flow_m3_h": self._design_flow,
            "contact_efficiency": round(efficiency, 4),
            "loop_running": loop_running,
        }
