"""锅炉烟气接入挡板。

挡板开启前炉膛必须已经建立负压。若先开挡板再升引风机，炉膛会短时正压，
灰从人孔门缝里吹出来。这里的顺序检查是硬性的：负压没到位就开挡板直接
拒绝，并把拒绝原因写进审计。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..errors import InterlockError, SequenceError
from ..eventbus import Event, EventBus
from ..fan.state import FanUnit


@dataclass
class BoilerFlue:
    """锅炉烟气挡板。"""

    damper_pct: float = 0.0
    open_count: int = 0
    close_count: int = 0

    @property
    def is_open(self) -> bool:
        return self.damper_pct > 0.0

    def drive(self, target_pct: float) -> float:
        """把挡板开到目标开度（0～100）。"""

        if not 0.0 <= target_pct <= 100.0:
            raise SequenceError("挡板开度必须在 0～100 之间", component="stack")
        self.damper_pct = float(target_pct)
        if target_pct == 0.0:
            self.close_count += 1
        else:
            self.open_count += 1
        return self.damper_pct

    def snapshot(self) -> dict:
        return {
            "damper_pct": round(self.damper_pct, 2),
            "is_open": self.is_open,
            "open_count": self.open_count,
            "close_count": self.close_count,
        }


def open_boiler_flue(
    fan: FanUnit, flue: BoilerFlue, bus: EventBus, opening_pct: float = 65.0
) -> float:
    """接入锅炉烟气。引风机未建立负压或未落盘时拒绝开挡板。"""

    if not fan.is_durable():
        raise InterlockError("引风机状态未落盘，禁止开锅炉烟气挡板", component="stack")
    if not fan.negative_pressure_ok():
        raise InterlockError(
            f"炉膛负压 {fan.pressure_kpa:.3f} kPa 未达下限，禁止开锅炉烟气挡板",
            component="stack",
        )
    position = flue.drive(opening_pct)
    bus.publish(
        Event.of(
            "stack",
            "stack",
            "boiler_flue_open",
            subject="boiler_flue",
            opening_pct=position,
            pressure_kpa=round(fan.pressure_kpa, 4),
        )
    )
    return position
