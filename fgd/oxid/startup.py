"""氧化风机投运。

氧化风机必须先于浆液循环泵投运；投运顺序写在启动顺序里，由这里统一发布
事件，便于审计按时间轴复盘"是先起风机还是先起泵"。
"""

from __future__ import annotations

from ..eventbus import Event, EventBus
from .blower import OxidationBlower


def start_oxidation(blower: OxidationBlower, bus: EventBus) -> float:
    """启动氧化风机并返回实际风量。"""

    flow = blower.start()
    bus.publish(
        Event.of(
            "oxid",
            "oxid",
            "oxidation_online",
            subject="oxid_blower",
            flow_m3_h=round(flow, 2),
        )
    )
    return flow
