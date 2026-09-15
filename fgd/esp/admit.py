"""通烟。

烟气进入除尘器之前必须确认电场已经升压；这个检查跨了除尘和引风两个组件：
电场侧给出"已带电"，引风机侧才能把烟气接入。顺序颠倒时粉尘未带电就穿过
电场，出口含尘量随负荷升高迅速超标。
"""

from __future__ import annotations

from ..eventbus import Event, EventBus
from ..fan.state import FanUnit
from .energize import EspField


def admit_flue(esp: EspField, fan: FanUnit, bus: EventBus) -> dict:
    """在电场带电之后把烟气接入引风机。"""

    esp.require_energized()
    pressure = fan.admit()
    payload = {
        "voltage_kv": round(esp.voltage_kv, 3),
        "pressure_kpa": round(pressure, 4),
    }
    bus.publish(
        Event.of("esp", "esp", "flue_admitted", subject="esp_field", **payload)
    )
    return payload
