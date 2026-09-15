"""清烟过程。

停运时引风机不能立刻停：先维持一段延时把炉膛和烟道里的残余烟气抽走，
负压回落到环境值以下才算"烟已走完"。浆液循环只有在拿到清烟凭据之后才
允许停泵，否则剩余 SO2 会直接穿过空塔。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..clock import Clock
from ..eventbus import Event, EventBus
from .state import FanUnit

DEFAULT_HOLD_S = 120.0


@dataclass(frozen=True)
class ClearToken:
    """清烟凭据：交给停运顺序的后续步骤作为前置条件。"""

    pressure_kpa: float
    hold_s: float
    cleared: bool

    def as_dict(self) -> dict:
        return {
            "pressure_kpa": round(self.pressure_kpa, 4),
            "hold_s": self.hold_s,
            "cleared": self.cleared,
        }


def clear_flue(
    fan: FanUnit,
    clock: Clock,
    bus: EventBus,
    hold_s: float = DEFAULT_HOLD_S,
) -> ClearToken:
    """延时清烟并返回凭据。

    先维持延时把余烟抽净，再把负压退到环境值；两者都完成后 ``cleared`` 才
    为真。
    """

    if hold_s < 0:
        raise ValueError("清烟延时不能为负")
    bus.publish(
        Event.of("fan", "fan", "clear_start", hold_s=hold_s, pressure=fan.pressure_kpa)
    )
    clock.sleep(hold_s)
    final_pressure = fan.clear()
    token = ClearToken(pressure_kpa=final_pressure, hold_s=hold_s, cleared=True)
    bus.publish(Event.of("fan", "fan", "clear_done", **token.as_dict()))
    return token
