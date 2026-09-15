"""烟气处理启动顺序。

启动顺序是整套系统的核心约束，按设计文档固定为：

1. 引风机建立负压；
2. 引风机负压状态落盘（下游只认已落盘的状态）；
3. 锅炉烟气挡板开启（负压到位后才允许接入烟气）；
4. 氨站建立供氨压力；
5. 脱硝喷枪投运（氨先于喷枪）；
6. 除尘电场升压；
7. 烟气接入除尘器（电场先于通烟）；
8. 氧化风机启动；
9. 浆液循环启动（氧化风机与落盘负压都到位）。

每一步都发事件到总线，运行完成后按记录的顺序自查：任何一步提前都会在
``verify_order`` 里被指出来。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..ammonia.supply import AmmoniaSupply
from ..clock import Clock
from ..denox.inject import AmmoniaInjector
from ..esp.admit import admit_flue
from ..esp.energize import EspField
from ..eventbus import Event, EventBus
from ..fan.state import FanUnit
from ..ns.topology import Topology
from ..oxid.blower import OxidationBlower
from ..oxid.startup import start_oxidation
from ..scrubber.loop import SlurryLoop
from ..stack.opening import BoilerFlue, open_boiler_flue

#: 启动顺序的硬约束：(先发生的步骤, 必须后发生的步骤, 说明)
ORDER_RULES: tuple[tuple[str, str, str], ...] = (
    ("fan_ramp", "stack_flue_open", "引风机负压必须先于锅炉烟气接入"),
    ("fan_persist", "scrubber_loop", "引风机负压必须先落盘再启动浆液循环"),
    ("ammonia_supply", "denox_inject", "供氨必须先于喷枪投运"),
    ("esp_energize", "esp_admit", "除尘电场必须先于通烟"),
    ("oxid_start", "scrubber_loop", "氧化风机必须先于浆液循环"),
    ("fan_ramp", "denox_inject", "负压建立后才允许脱硝投运"),
)

#: 步骤归属的组件名，用于对照链路拓扑。
STEP_COMPONENT: dict[str, str] = {
    "fan_ramp": "fan",
    "fan_persist": "fan",
    "stack_flue_open": "stack",
    "ammonia_supply": "ammonia",
    "denox_inject": "denox",
    "esp_energize": "esp",
    "esp_admit": "fan",
    "oxid_start": "oxid",
    "scrubber_loop": "scrubber",
}

#: 拓扑里必须存在的上游／下游关系。
TOPOLOGY_RULES: tuple[tuple[str, str], ...] = (
    ("fan", "denox"),
    ("fan", "absorber"),
    ("oxid", "scrubber"),
    ("denox", "esp"),
)


@dataclass
class StartupSequence:
    """启动顺序执行器。"""

    clock: Clock
    bus: EventBus
    fan: FanUnit
    flue: BoilerFlue
    supply: AmmoniaSupply
    injector: AmmoniaInjector
    esp: EspField
    blower: OxidationBlower
    loop: SlurryLoop
    ammonia_pressure_kpa: float = 0.42
    esp_voltage_kv: float = 58.0
    executed: list[str] = field(default_factory=list)

    def _note(self, step: str, **detail: object) -> None:
        self.executed.append(step)
        self.bus.publish(
            Event.of(
                "sequence",
                "sequence",
                "startup_step",
                subject=step,
                step_index=len(self.executed),
                **detail,
            )
        )

    def run(self) -> dict:
        """按顺序执行启动步骤，返回执行报告。"""

        self.executed.clear()
        self.fan.ramp_to_negative_pressure()
        self._note("fan_ramp", pressure_kpa=round(self.fan.pressure_kpa, 4))

        digest = self.fan.persist_state()
        self._note("fan_persist", digest=digest[:12])

        position = open_boiler_flue(self.fan, self.flue, self.bus)
        self._note("stack_flue_open", opening_pct=position)

        self.supply.establish(self.ammonia_pressure_kpa)
        self._note("ammonia_supply", pressure_kpa=self.ammonia_pressure_kpa)

        rate = self.injector.start_injection(self.supply)
        self._note("denox_inject", rate_kg_h=rate)

        self.esp.energize(self.esp_voltage_kv)
        self._note("esp_energize", voltage_kv=self.esp_voltage_kv)

        admit = admit_flue(self.esp, self.fan, self.bus)
        self._note("esp_admit", **admit)

        flow = start_oxidation(self.blower, self.bus)
        self._note("oxid_start", flow_m3_h=round(flow, 2))

        loop_flow = self.loop.start(self.blower, self.fan)
        self._note("scrubber_loop", flow_m3_h=round(loop_flow, 2))

        return self.report()

    def verify_order(self) -> list[str]:
        """按执行记录自查顺序约束，返回违规说明列表（空表示合规）。"""

        violations: list[str] = []
        for earlier, later, description in ORDER_RULES:
            if earlier not in self.executed or later not in self.executed:
                violations.append(f"{description}：步骤 {earlier} 或 {later} 未执行")
                continue
            if self.executed.index(earlier) > self.executed.index(later):
                violations.append(f"{description}：实际先执行了 {later}")
        return violations

    def verify_topology(self, topology: Topology) -> list[str]:
        """确认链路拓扑里确实声明了这些上下游关系。"""

        missing: list[str] = []
        for upstream, downstream in TOPOLOGY_RULES:
            if not topology.requires_before(upstream, downstream):
                missing.append(f"拓扑缺少 {upstream} -> {downstream}")
        return missing

    def report(self) -> dict:
        return {
            "steps": list(self.executed),
            "violations": self.verify_order(),
            "fan_pressure_kpa": round(self.fan.pressure_kpa, 4),
            "esp_voltage_kv": round(self.esp.voltage_kv, 3),
            "supply_pressure_kpa": round(self.supply.pressure_kpa, 4),
            "loop_flow_m3_h": round(self.loop.flow_m3_h, 2),
        }
