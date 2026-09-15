"""平台装配与控制循环。

``Platform`` 把各组件按真实调用链接起来，并暴露三件事：启动／停运顺序、
控制循环和状态快照。控制循环每个周期做六件事：两个负压回路结算引风机转速、
NOx 回路与氨站安全联锁结算喷氨阀、循环泵按 pH 补浆、氧化风把亚硫酸盐转成
硫酸盐、电场按周期振打、最后按当前工况折算排放并用最新标定基线判定。
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass

from .absorber.contact import AbsorberTower
from .absorber.mist import MistEliminator
from .ammonia.safety import AmmoniaSafety
from .ammonia.supply import AmmoniaSupply
from .ammonia.valve import AmmoniaValve
from .audit.log import AuditLog
from .audit.record import AuditRecord
from .clock import Clock, SystemClock
from .config import PlatformConfig, default_config
from .denox.catalyst import Catalyst
from .denox.inject import AmmoniaInjector
from .denox.loop import NoxLoop
from .errors import FgdError
from .esp.energize import EspField
from .esp.rapping import RappingCycle
from .eventbus import Event, EventBus
from .fan.draft import BoilerDraftLoop, FgdDraftLoop, PressureController
from .fan.speed import SpeedArbiter
from .fan.state import FanUnit
from .lime.feed import LimeFeed
from .lime.slurry import Slurry
from .ns.registry import NamespaceRegistry
from .ns.topology import Topology, build_default_topology
from .oxid.blower import OxidationBlower
from .scrubber.circulate import CirculationPump
from .scrubber.latch import PhLatch
from .scrubber.loop import SlurryLoop
from .scrubber.offset import AnalyzerOffset
from .sequence.shutdown import ShutdownSequence
from .sequence.startup import StartupSequence
from .stack.control import EmissionReading, StackEmissionControl
from .stack.opening import BoilerFlue
from .store.journal import JsonlJournal
from .store.jsonfile import JsonStore
from .telemetry import TrendBuffer

DEFAULT_UNIT = "unit1"
DEFAULT_OXID_RATED_FLOW_M3_H = 4800.0
DEFAULT_LOOP_FLOW_M3_H = 9600.0


@dataclass(frozen=True)
class CycleInputs:
    """一个控制周期的工况输入。"""

    load: float
    nox_target: float
    ammonia_concentration_ppm: float
    dt: float

    def clamped(self) -> "CycleInputs":
        return CycleInputs(
            load=max(0.0, min(1.0, float(self.load))),
            nox_target=max(1.0, float(self.nox_target)),
            ammonia_concentration_ppm=max(0.0, float(self.ammonia_concentration_ppm)),
            dt=max(0.001, float(self.dt)),
        )


class Platform:
    """烟气处理控制平台。"""

    def __init__(
        self,
        config: PlatformConfig | None = None,
        clock: Clock | None = None,
        unit: str = DEFAULT_UNIT,
    ) -> None:
        self.config = config or default_config()
        self.clock = clock or SystemClock()
        self.bus = EventBus()

        store = JsonStore(self.config.storage_root)
        self.store = store
        self.audit = AuditLog(
            self.clock, self.bus, JsonlJournal(store.root, "audit", segment_limit=512)
        )
        self.namespaces = NamespaceRegistry(unit)
        self.topology: Topology = build_default_topology()
        self.trend = TrendBuffer(period_s=5.0)

        self.fan = FanUnit(
            clock=self.clock,
            bus=self.bus,
            store=store,
            target_kpa=self.config.fan.negative_pressure_target_kpa,
            floor_kpa=self.config.fan.negative_pressure_floor_kpa,
            rate_kpa_per_s=self.config.fan.ramp_rate_kpa_per_s,
        )
        self.fan.restore()
        self.flue = BoilerFlue()
        self.slurry = Slurry(ph=self.config.scrubber.ph_target)
        self.lime = LimeFeed(self.bus, self.slurry, self.config.lime)
        self.blower = OxidationBlower(self.bus, DEFAULT_OXID_RATED_FLOW_M3_H)
        self.loop = SlurryLoop(self.bus, DEFAULT_LOOP_FLOW_M3_H)
        self.latch = PhLatch(self.bus, self.config.scrubber)
        self.circulation = CirculationPump(
            limits=self.config.scrubber,
            latch=self.latch,
            feed=self.lime,
            flow_m3_h=DEFAULT_LOOP_FLOW_M3_H,
        )
        self.absorber = AbsorberTower(
            DEFAULT_LOOP_FLOW_M3_H, ph_reference=self.config.scrubber.ph_low
        )
        self.mist = MistEliminator(field_kv_min=self.config.esp.field_kv_min)
        self.esp = EspField(self.bus, self.config.esp)
        self.rapping = RappingCycle(self.clock, self.bus, self.config.esp)
        self.catalyst = Catalyst()
        self.injector = AmmoniaInjector(
            self.bus, self.catalyst, self.config.ammonia.min_rate_kg_h
        )
        self.supply = AmmoniaSupply(self.bus, self.config.ammonia)
        self.valve = AmmoniaValve(
            self.config.ammonia.min_rate_kg_h, self.config.ammonia.max_rate_kg_h
        )
        self.safety = AmmoniaSafety(self.bus, self.config.ammonia, self.valve, self.supply)
        self.nox_loop = NoxLoop(
            self.valve, base_rate_kg_h=self.config.ammonia.min_rate_kg_h * 2, kp=1.4, ki=0.25
        )
        self.offset = AnalyzerOffset(
            self.config.analyzer.default_offset_mg_m3, self.clock.now()
        )
        self.emission = StackEmissionControl(
            self.clock,
            self.bus,
            self.config.emission,
            self.config.analyzer,
            self.offset,
            self.trend,
        )

        self.arbiter = SpeedArbiter(
            self.config.fan.min_speed_rpm, self.config.fan.max_speed_rpm
        )
        self.boiler_draft = BoilerDraftLoop(
            self.arbiter,
            PressureController(kp=620.0, ki=85.0, output_min=-260.0, output_max=320.0),
            base_rpm=980.0,
        )
        self.fgd_draft = FgdDraftLoop(
            self.arbiter,
            PressureController(kp=410.0, ki=60.0, output_min=-180.0, output_max=240.0),
            base_rpm=1010.0,
        )

        self.startup = StartupSequence(
            clock=self.clock,
            bus=self.bus,
            fan=self.fan,
            flue=self.flue,
            supply=self.supply,
            injector=self.injector,
            esp=self.esp,
            blower=self.blower,
            loop=self.loop,
            esp_voltage_kv=(self.config.esp.field_kv_min + self.config.esp.field_kv_max) / 2,
        )
        self.shutdown = ShutdownSequence(
            clock=self.clock,
            bus=self.bus,
            fan=self.fan,
            flue=self.flue,
            loop=self.loop,
            slurry=self.slurry,
            blower=self.blower,
            esp=self.esp,
            injector=self.injector,
            supply=self.supply,
        )
        self._cycles = 0
        self._lock = threading.RLock()
        self._register_namespaces()

    def _register_namespaces(self) -> None:
        """登记现场设备命名空间（控制台与审计共用同一套名字）。"""

        for area, device, kind in (
            ("flue", "fan", "induced-fan"),
            ("flue", "boiler_flue", "damper"),
            ("denox", "injector", "spray-grid"),
            ("denox", "catalyst", "catalyst-layer"),
            ("ammonia", "supply", "supply-skid"),
            ("ammonia", "valve", "control-valve"),
            ("esp", "field", "electric-field"),
            ("scrubber", "loop", "circulation-pump"),
            ("scrubber", "latch", "ph-interlock"),
            ("scrubber", "analyzer", "gas-analyzer"),
            ("absorber", "tower", "spray-tower"),
            ("absorber", "mist", "mist-eliminator"),
            ("lime", "slurry", "slurry-pond"),
            ("oxid", "blower", "oxidation-fan"),
            ("stack", "emission", "cem-stack"),
        ):
            self.namespaces.register(area, device, kind)

    def start(self) -> dict:
        """执行启动顺序。"""

        with self._lock:
            report = self.startup.run()
            report["topology_missing"] = self.startup.verify_topology(self.topology)
            return report

    def stop(self) -> dict:
        """执行停运顺序。"""

        with self._lock:
            return self.shutdown.run()

    def cycle(self, inputs: CycleInputs) -> dict:
        """推进一个控制周期，返回本周期摘要。"""

        state = inputs.clamped()
        with self._lock:
            self._cycles += 1
            moment = self.clock.now()

            boiler = self.boiler_draft.step(
                self.fan.pressure_kpa,
                self.config.fan.negative_pressure_target_kpa,
                state.dt,
            )
            fgd = self.fgd_draft.step(
                self.fan.pressure_kpa,
                self.config.fan.negative_pressure_floor_kpa,
                state.dt,
            )
            arbitration = self.arbiter.resolve()
            self.trend.append("fan_speed", moment, arbitration.rpm)
            self.trend.append("fan_pressure", moment, self.fan.pressure_kpa)
            self.trend.append("load", moment, state.load)

            nox_measured = self._nox_before_scrubber(state.load)
            safety = self.safety.check(
                state.ammonia_concentration_ppm, self.supply.pressure_kpa
            )
            valve_outcome = self.nox_loop.step(nox_measured, state.nox_target, state.dt)
            self.injector.set_rate(valve_outcome.rate_kg_h)

            ph = self.slurry.ph
            circulation = self.circulation.step(ph)

            sulfite = self.slurry.oxidize(self.blower.flow_m3_h, state.load)
            rapping = self.rapping.run_if_due(self.esp.voltage_kv)

            absorb = self.absorber.absorb(
                2000.0 * state.load, ph, self.loop.flow_m3_h, self.loop.running
            )
            self.slurry.absorb_so2(state.load, absorb.removal)
            verdict = self._evaluate_emission(state, absorb)

            return {
                "cycle": self._cycles,
                "dt": state.dt,
                "load": round(state.load, 4),
                "boiler_draft": boiler.as_dict(),
                "fgd_draft": fgd.as_dict(),
                "fan_arbitration": arbitration.as_dict(),
                "ammonia_safety": safety.as_dict() if safety else None,
                "nox_valve": valve_outcome.as_dict(),
                "circulation": circulation,
                "absorber": absorb.as_dict(),
                "sulfite_ppm": round(sulfite, 3),
                "rapping": rapping,
                "emission": verdict.as_dict(),
            }

    def _nox_before_scrubber(self, load: float) -> float:
        """按负荷与催化剂效率推算进入吸收塔之前的 NOx。"""

        raw_nox = 420.0 * load + 18.0
        return raw_nox * (1.0 - self.catalyst.efficiency())

    def _evaluate_emission(self, state: CycleInputs, absorb):
        """按工况折算烟气并交给排放判定。"""

        dust_result = self.mist.capture(
            3150.0 * state.load, self.esp.voltage_kv, self.esp.is_energized()
        )
        offset = self.offset.current()
        reading = EmissionReading(
            so2_mg_m3=absorb.so2_out_mg_m3 + offset,
            nox_mg_m3=self._nox_before_scrubber(state.load) + offset,
            dust_mg_m3=dust_result.dust_out_mg_m3,
        )
        return self.emission.evaluate(reading)

    def concurrency_probe(self, rounds: int = 24) -> dict:
        """用真实并行调用探测两个共享执行器的仲裁结果。

        两个线程分别代表锅炉负压回路与脱硫压降回路，同时向引风机提交转速；
        另两个线程分别代表 NOx 自动调节与氨站安全联锁，同时写喷氨阀。探测只
        读取最终提交值，用来确认共享执行器始终收敛到唯一值。
        """

        if rounds < 1:
            raise ValueError("探测轮数必须为正")
        pressure_errors: list[float] = []
        valve_rates: list[float] = []

        def fan_worker(loop, target, dt):
            for _ in range(rounds):
                loop.step(self.fan.pressure_kpa, target, dt)

        def valve_worker(writer, rate, priority):
            for _ in range(rounds):
                self.valve.request(writer, rate, priority)

        threads = [
            threading.Thread(
                target=fan_worker,
                args=(self.boiler_draft, self.config.fan.negative_pressure_target_kpa, 0.5),
            ),
            threading.Thread(
                target=fan_worker,
                args=(self.fgd_draft, self.config.fan.negative_pressure_floor_kpa, 0.5),
            ),
            threading.Thread(
                target=valve_worker,
                args=("nox_loop", self.config.ammonia.min_rate_kg_h * 3, 50),
            ),
            threading.Thread(
                target=valve_worker,
                args=("safety", self.safety.safe_cap(), 80),
            ),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        speed = self.arbiter.resolve()
        valve = self.valve.resolve()
        pressure_errors.append(speed.rpm)
        valve_rates.append(valve.rate_kg_h)
        return {
            "rounds": rounds,
            "speed_winner": speed.winner,
            "committed_rpm": round(speed.rpm, 2),
            "speed_samples": len(pressure_errors),
            "valve_winner": valve.winner,
            "committed_rate_kg_h": round(valve.rate_kg_h, 3),
            "valve_samples": len(valve_rates),
        }

    def calibration_probe(self, offset_mg_m3: float) -> dict:
        """重新标定分析仪，返回标定前后的判定差异。"""

        before_epoch = self.offset.epoch
        before_offset = self.offset.current()
        self.offset.calibrate(offset_mg_m3, self.clock.now())
        self.bus.publish(
            Event.of(
                "scrubber",
                "scrubber",
                "analyzer_recalibrated",
                subject="analyzer",
                severity="notice",
                offset_mg_m3=offset_mg_m3,
                epoch=self.offset.epoch,
            )
        )
        return {
            "epoch_before": before_epoch,
            "epoch_after": self.offset.epoch,
            "offset_before": before_offset,
            "offset_after": self.offset.current(),
        }

    def regenerate_catalyst(self, reason: str = "检修再生") -> dict:
        """催化剂检修再生：清掉空喷沉积并把效率恢复到设计值。"""

        before = self.catalyst.efficiency()
        after = self.catalyst.regenerate()
        self.bus.publish(
            Event.of(
                "denox",
                "denox",
                "catalyst_regenerated",
                subject="catalyst",
                severity="notice",
                reason=reason,
                efficiency_before=before,
                efficiency_after=after,
            )
        )
        return {"efficiency_before": before, "efficiency_after": after}

    def note_error(self, error: FgdError) -> dict:
        """把平台异常写入审计，返回落盘记录。"""

        payload = error.as_record()
        record = self.audit.record(
            AuditRecord.create(
                self.clock.now(),
                payload["component"],
                "error",
                payload["type"],
                severity="warning",
                message=payload["message"],
            )
        )
        return record.as_dict()

    def reset_state(self) -> bool:
        """清掉落盘的引风机状态（检修后重新标定用）。"""

        return self.store.remove("fan_state")

    def status(self) -> dict:
        """汇总状态快照，供控制台与命令行读取。"""

        return {
            "platform": self.config.name,
            "project_id": self.config.project_id,
            "cycles": self._cycles,
            "now": round(self.clock.now(), 3),
            "utc": self.clock.utc_iso(),
            "fan": self.fan.snapshot(),
            "flue": self.flue.snapshot(),
            "esp": self.esp.snapshot(),
            "rapping": self.rapping.snapshot(),
            "ammonia": self.supply.snapshot(),
            "valve": self.valve.snapshot(),
            "safety": self.safety.snapshot(),
            "nox_loop": self.nox_loop.snapshot(),
            "denox": self.injector.snapshot(),
            "catalyst": self.catalyst.snapshot(),
            "scrubber": {
                "loop": self.loop.snapshot(),
                "latch": self.latch.snapshot(),
                "offset": self.offset.snapshot(),
            },
            "slurry": self.slurry.snapshot(),
            "lime": self.lime.snapshot(),
            "oxid": self.blower.snapshot(),
            "absorber": self.absorber.snapshot(
                self.slurry.ph, self.loop.flow_m3_h, self.loop.running
            ),
            "emission": self.emission.latest(),
            "arbiter": self.arbiter.snapshot(),
            "arbiter_recent": self.arbiter.decisions(10),
            "draft_loops": [self.boiler_draft.snapshot(), self.fgd_draft.snapshot()],
            "fan_ramp_trace": self.fan.trace(),
            "emission_breaches": self.emission.breach_count(),
            "namespaces_by_area": self.namespaces_by_area(),
            "audit": self.audit.summary(),
            "endpoints": self.endpoints(),
        }

    def namespaces_by_area(self) -> dict:
        """按区域分组返回命名空间，控制台据此渲染分区清单。"""

        areas = sorted({entry["area"] for entry in self.namespaces.describe()})
        return {
            area: [entry.as_dict() for entry in self.namespaces.in_area(area)]
            for area in areas
        }

    def endpoints(self) -> dict:
        """返回本平台对外暴露的页面与接口清单。"""

        return {
            "pages": list(self.config.pages),
            "api": [
                "/api/status",
                "/api/trend",
                "/api/alarms",
                "/api/audit",
                "/api/journal",
                "/api/readiness",
                "/api/start",
                "/api/stop",
                "/api/step",
                "/api/calibrate",
                "/api/maintenance/catalyst",
            ],
        }

    def topology_summary(self) -> dict:
        """链路拓扑加上每个节点的上下游关系。"""

        summary = self.topology.as_dict()
        summary["upstream"] = {
            name: self.topology.upstream_of(name) for name in self.topology.nodes
        }
        summary["downstream"] = {
            name: self.topology.downstream_of(name) for name in self.topology.nodes
        }
        return summary


def build_platform(
    config: PlatformConfig | None = None,
    clock: Clock | None = None,
    storage_root: str | None = None,
    unit: str = DEFAULT_UNIT,
) -> Platform:
    """构造平台；``storage_root`` 给出时覆盖配置里的存储目录。"""

    effective = config or default_config()
    if storage_root:
        effective = PlatformConfig(
            name=effective.name,
            project_id=effective.project_id,
            storage_root=os.path.abspath(storage_root),
            fan=effective.fan,
            esp=effective.esp,
            scrubber=effective.scrubber,
            ammonia=effective.ammonia,
            emission=effective.emission,
            analyzer=effective.analyzer,
            lime=effective.lime,
            pages=effective.pages,
        )
    return Platform(config=effective, clock=clock, unit=unit)
