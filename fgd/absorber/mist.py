"""除雾器与含尘折算。

含尘量由两级决定：除尘电场的带电捕集，以及吸收塔顶部除雾器对残余粉尘与
雾滴的捕集。电场未升压时带电捕集为零，除雾器单独工作留不住高浓度粉尘，
出口含尘量随负荷线性上升。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MistResult:
    """一次除雾计算的结果。"""

    dust_out_mg_m3: float
    field_capture: float
    mist_capture: float

    def as_dict(self) -> dict:
        return {
            "dust_out_mg_m3": round(self.dust_out_mg_m3, 3),
            "field_capture": round(self.field_capture, 4),
            "mist_capture": round(self.mist_capture, 4),
        }


class MistEliminator:
    """吸收塔顶部除雾器。"""

    MIST_CAPTURE = 0.55
    FIELD_CAPTURE_MIN = 0.995
    FIELD_CAPTURE_MAX = 0.9995

    def __init__(self, field_kv_min: float = 42.0, reference_kv: float = 60.0) -> None:
        if field_kv_min <= 0 or reference_kv <= field_kv_min:
            raise ValueError("电场起晕电压必须小于参考电压")
        self._field_kv_min = float(field_kv_min)
        self._reference_kv = float(reference_kv)

    @property
    def field_kv_min(self) -> float:
        return self._field_kv_min

    def field_capture(self, voltage_kv: float, energized: bool) -> float:
        """按电场电压算带电捕集率；未升压或电压低于起晕值时按 0 处理。"""

        if not energized or voltage_kv < self._field_kv_min:
            return 0.0
        headroom = (voltage_kv - self._field_kv_min) / (
            self._reference_kv - self._field_kv_min
        )
        ratio = min(1.0, max(0.0, headroom))
        return round(self.FIELD_CAPTURE_MIN + (self.FIELD_CAPTURE_MAX - self.FIELD_CAPTURE_MIN) * ratio, 6)

    def capture(self, dust_in_mg_m3: float, voltage_kv: float, energized: bool) -> MistResult:
        """计算除雾器出口含尘量。"""

        field = self.field_capture(voltage_kv, energized)
        after_field = dust_in_mg_m3 * (1.0 - field)
        mist = self.MIST_CAPTURE if after_field > 0 else 0.0
        return MistResult(
            dust_out_mg_m3=after_field * (1.0 - mist),
            field_capture=field,
            mist_capture=mist,
        )
