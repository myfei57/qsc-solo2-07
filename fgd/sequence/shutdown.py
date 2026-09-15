"""烟气处理停运顺序。

停运顺序固定为：

1. 关锅炉烟气挡板（先停烟）；
2. 引风机延时清烟，直到拿到清烟凭据；
3. 浆液循环冲洗；
4. 停浆液循环（烟气走完之后才切泵）；
5. 排空浆液；
6. 停氧化风机；
7. 除尘电场泄压；
8. 停脱硝喷枪；
9. 切断供氨。

先切泵后停烟会让残余 SO2 穿过空塔，顺序自查会把这个错误直接指出来。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..ammonia.supply import AmmoniaSupply
from ..clock import Clock
from ..denox.inject import AmmoniaInjector
from ..esp.energize import EspField
from ..eventbus import Event, EventBus
from ..fan.clear import DEFAULT_HOLD_S, clear_flue
from ..fan.state import FanUnit
from ..lime.slurry import Slurry
from ..oxid.blower import OxidationBlower
from ..scrubber.cut import cut_loop
from ..scrubber.loop import SlurryLoop
from ..stack.opening import BoilerFlue

ORDER_RULES: tuple[tuple[str, str, str], ...] = (
    ("boiler_flue_close", "fan_clear", "先停烟再清烟"),
    ("fan_clear", "scrubber_cut", "烟气走完之后才允许切浆液循环"),
    ("scrubber_cut", "slurry_drain", "切泵之后才排空浆液"),
)


@dataclass
class ShutdownSequence:
    """停运顺序执行器。"""

    clock: Clock
    bus: EventBus
    fan: FanUnit
    flue: BoilerFlue
    loop: SlurryLoop
    slurry: Slurry
    blower: OxidationBlower
    esp: EspField
    injector: AmmoniaInjector
    supply: AmmoniaSupply
    flush_s: float = 60.0
    clear_hold_s: float = DEFAULT_HOLD_S
    executed: list[str] = field(default_factory=list)

    def _note(self, step: str, **detail: object) -> None:
        self.executed.append(step)
        self.bus.publish(
            Event.of(
                "sequence",
                "sequence",
                "shutdown_step",
                subject=step,
                step_index=len(self.executed),
                **detail,
            )
        )

    def run(self) -> dict:
        """按顺序执行停运步骤，返回执行报告。"""

        self.executed.clear()

        opening = self.flue.drive(0.0)
        self._note("boiler_flue_close", opening_pct=opening)

        token = clear_flue(self.fan, self.clock, self.bus, self.clear_hold_s)
        self._note("fan_clear", **token.as_dict())

        flushed = self.loop.flush(self.flush_s)
        self._note("slurry_flush", duration_s=flushed)

        cut = cut_loop(self.loop, token, self.bus)
        self._note("scrubber_cut", **cut)

        drained = self.slurry.drain()
        self._note("slurry_drain", density_pct=round(drained, 3))

        self.blower.stop()
        self._note("oxid_stop")

        self.esp.de_energize()
        self._note("esp_off")

        self.injector.stop()
        self._note("denox_stop")

        self.supply.shutoff("停运顺序切断供氨")
        self._note("ammonia_shutoff")

        return self.report()

    def verify_order(self) -> list[str]:
        """按执行记录自查停运顺序，返回违规说明列表。"""

        violations: list[str] = []
        for earlier, later, description in ORDER_RULES:
            if earlier not in self.executed or later not in self.executed:
                violations.append(f"{description}：步骤 {earlier} 或 {later} 未执行")
                continue
            if self.executed.index(earlier) > self.executed.index(later):
                violations.append(f"{description}：实际先执行了 {later}")
        return violations

    def report(self) -> dict:
        return {
            "steps": list(self.executed),
            "violations": self.verify_order(),
            "drawer_density_pct": round(self.slurry.density_pct, 3),
            "sphere_voltage_kv": round(self.esp.voltage_kv, 3),
            "loop_running": self.loop.running,
        }
