"""停运切泵。

浆液循环只能在烟气走完之后才允许停：先停泵、后停烟，残余 SO2 会直接穿过
空塔。切泵之前必须拿到清烟凭据，凭据缺失或未清完一律拒绝。
"""

from __future__ import annotations

from ..eventbus import Event, EventBus
from ..errors import SequenceError
from ..fan.clear import ClearToken
from .loop import SlurryLoop


def cut_loop(loop: SlurryLoop, token: ClearToken, bus: EventBus) -> dict:
    """在拿到清烟凭据之后停止浆液循环。"""

    if not token.cleared:
        raise SequenceError("烟气尚未走完，禁止停浆液循环", component="scrubber")
    loop.require_running()
    loop.stop()
    payload = {
        "clear_pressure_kpa": round(token.pressure_kpa, 4),
        "hold_s": token.hold_s,
    }
    bus.publish(
        Event.of(
            "scrubber", "scrubber", "slurry_loop_cut", subject="slurry_loop", **payload
        )
    )
    return payload
