"""引风机状态机与落盘。

负压状态是整条烟气链的前置条件：脱硝、除尘和脱硫都要在看到"本机已确认的
负压"之后才允许动作。因此状态机区分"已就绪"和"已落盘"两件事——只有落盘
成功的状态才允许后续组件读取，控制器重启后也按落盘内容恢复。
"""

from __future__ import annotations

import enum
import threading

from ..clock import Clock
from ..errors import InterlockError, SequenceError
from ..eventbus import Event, EventBus
from ..store.jsonfile import JsonStore
from .ramp import RampTrace, execute_ramp, plan_ramp

STATE_KEY = "fan_state"
RAMP_STEP_S = 2.0


class FanPhase(str, enum.Enum):
    """引风机相位。"""

    STOPPED = "stopped"
    RAMPING = "ramping"
    READY = "ready"
    ADMITTED = "admitted"
    CLEARING = "clearing"


class FanStateMachine:
    """记录相位迁移，非法迁移直接拒绝。"""

    _ALLOWED = {
        FanPhase.STOPPED: (FanPhase.RAMPING,),
        FanPhase.RAMPING: (FanPhase.READY, FanPhase.STOPPED),
        # 控制器重启后可能带来一份 READY/ADMITTED 的历史状态，此时重新升压或
        # 清烟都是合法的恢复路径。
        FanPhase.READY: (FanPhase.RAMPING, FanPhase.ADMITTED, FanPhase.CLEARING),
        FanPhase.ADMITTED: (FanPhase.CLEARING,),
        FanPhase.CLEARING: (FanPhase.STOPPED,),
    }

    def __init__(self) -> None:
        self._phase = FanPhase.STOPPED
        self._history: list[FanPhase] = [FanPhase.STOPPED]

    @property
    def phase(self) -> FanPhase:
        return self._phase

    def transition(self, target: FanPhase, component: str = "fan") -> FanPhase:
        if target == self._phase:
            return self._phase
        if target not in self._ALLOWED[self._phase]:
            raise SequenceError(
                f"引风机不允许从 {self._phase.value} 迁移到 {target.value}",
                component=component,
            )
        self._phase = target
        self._history.append(target)
        return self._phase

    def history(self) -> list[str]:
        return [phase.value for phase in self._history]

    def snapshot(self) -> dict:
        return {"phase": self._phase.value, "history": self.history()}

    def restore(self, snapshot: dict) -> None:
        """从落盘快照恢复相位。"""

        phase = snapshot.get("phase")
        if not isinstance(phase, str):
            raise SequenceError("引风机快照缺少相位", component="fan")
        try:
            restored = FanPhase(phase)
        except ValueError as exc:
            raise SequenceError(f"未知引风机相位: {phase}", component="fan") from exc
        self._phase = restored
        self._history = [FanPhase.STOPPED, restored]


class FanUnit:
    """引风机本体：负压、落盘、通烟与清烟。"""

    def __init__(
        self,
        clock: Clock,
        bus: EventBus,
        store: JsonStore,
        target_kpa: float,
        floor_kpa: float,
        rate_kpa_per_s: float,
    ) -> None:
        self._clock = clock
        self._bus = bus
        self._store = store
        self._target = float(target_kpa)
        self._floor = float(floor_kpa)
        self._rate = float(rate_kpa_per_s)
        self._state = FanStateMachine()
        self._pressure = 0.0
        self._durable = False
        self._restored_from_disk = False
        self._trace = RampTrace()
        self._lock = threading.RLock()

    @property
    def pressure_kpa(self) -> float:
        return self._pressure

    @property
    def phase(self) -> FanPhase:
        return self._state.phase

    @property
    def restored_from_disk(self) -> bool:
        return self._restored_from_disk

    def negative_pressure_ok(self) -> bool:
        """负压是否已达到工艺下限（越负越好）。"""

        return self._pressure <= self._floor

    def ramp_to_negative_pressure(self) -> float:
        """从当前负压升到工作负压。"""

        with self._lock:
            self._state.transition(FanPhase.RAMPING)
            self._bus.publish(
                Event.of("fan", "fan", "ramp_start", target=self._target, pressure=self._pressure)
            )
            self._durable = False
            plan = plan_ramp(self._pressure, self._target, self._rate)
            self._trace = RampTrace()
            self._pressure = execute_ramp(plan, self._clock, RAMP_STEP_S, self._trace.note)
            self._state.transition(FanPhase.READY)
            self._bus.publish(
                Event.of(
                    "fan",
                    "fan",
                    "ramp_done",
                    pressure=self._pressure,
                    duration_s=round(plan.duration_s, 3),
                )
            )
            return self._pressure

    def persist_state(self) -> str:
        """把当前相位与负压落盘，返回内容摘要。"""

        with self._lock:
            snapshot = self._state.snapshot()
            snapshot["pressure_kpa"] = round(self._pressure, 4)
            snapshot["target_kpa"] = self._target
            snapshot["floor_kpa"] = self._floor
            snapshot["revision"] = len(snapshot["history"])
            digest = self._store.write(STATE_KEY, snapshot)
            self._durable = True
        self._bus.publish(
            Event.of("fan", "fan", "state_durable", subject=STATE_KEY, digest=digest[:12])
        )
        return digest

    def is_durable(self) -> bool:
        """落盘标记为真且磁盘上确实存在对应文档。"""

        with self._lock:
            return bool(
                self._durable
                and self._store.exists(STATE_KEY)
                and self._store.digest(STATE_KEY)
            )

    def require_durable(self) -> None:
        """后续组件在动作前必须确认负压已落盘。"""

        if not self.is_durable():
            raise InterlockError("引风机负压尚未落盘，禁止下游动作", component="fan")

    def restore(self) -> bool:
        """从磁盘恢复上次落盘的负压状态。"""

        snapshot = self._store.read(STATE_KEY)
        if not snapshot:
            return False
        with self._lock:
            self._state.restore(snapshot)
            pressure = snapshot.get("pressure_kpa")
            if isinstance(pressure, (int, float)):
                self._pressure = float(pressure)
            self._durable = True
            self._restored_from_disk = True
        self._bus.publish(
            Event.of(
                "fan",
                "fan",
                "state_restored",
                subject=STATE_KEY,
                phase=self._state.phase.value,
                pressure=self._pressure,
            )
        )
        return True

    def admit(self) -> float:
        """接入锅炉烟气：必须已经达到负压并落盘。"""

        self.require_durable()
        if not self.negative_pressure_ok():
            raise InterlockError(
                f"炉膛负压 {self._pressure:.3f} kPa 未达下限 {self._floor:.3f} kPa",
                component="fan",
            )
        with self._lock:
            self._state.transition(FanPhase.ADMITTED)
        self._bus.publish(
            Event.of("fan", "fan", "flue_admitted", pressure=self._pressure)
        )
        return self._pressure

    def clear(self) -> float:
        """清烟：把负压退回环境值，返回终点负压。"""

        with self._lock:
            self._state.transition(FanPhase.CLEARING)
            plan = plan_ramp(self._pressure, 0.0, self._rate)
            self._pressure = execute_ramp(plan, self._clock, RAMP_STEP_S)
            self._state.transition(FanPhase.STOPPED)
            self._durable = False
        self._bus.publish(
            Event.of("fan", "fan", "flue_cleared", pressure=self._pressure)
        )
        return self._pressure

    def trace(self) -> dict:
        """返回最近一次升压过程的采样轨迹。"""

        with self._lock:
            return self._trace.as_dict()

    def snapshot(self) -> dict:
        """当前状态快照，供控制台与状态接口使用。"""

        with self._lock:
            return {
                "phase": self._state.phase.value,
                "history": self._state.history(),
                "pressure_kpa": round(self._pressure, 4),
                "target_kpa": self._target,
                "floor_kpa": self._floor,
                "durable": self.is_durable(),
                "negative_pressure_ok": self.negative_pressure_ok(),
                "restored_from_disk": self._restored_from_disk,
            }
