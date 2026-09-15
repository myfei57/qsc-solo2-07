"""喷氨调节阀仲裁。

NOx 自动调节回路和氨站安全联锁都会写同一个调节阀。两个写入方分别在不同
线程按自己的周期动作，必须由仲裁器选出唯一开度：安全联锁优先级高于自动
调节，联锁动作时自动调节的请求被记录为被拒，而不是互相覆盖。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ValveRequest:
    """一次阀门开度请求（喷氨率，kg/h）。"""

    writer: str
    rate_kg_h: float
    priority: int
    sequence: int
    issued_at: float

    def as_dict(self) -> dict:
        return {
            "writer": self.writer,
            "rate_kg_h": round(self.rate_kg_h, 3),
            "priority": self.priority,
        }


@dataclass
class ValveOutcome:
    """一次阀门仲裁结果。"""

    winner: str
    rate_kg_h: float
    superseded: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "winner": self.winner,
            "rate_kg_h": round(self.rate_kg_h, 3),
            "superseded": list(self.superseded),
        }


class AmmoniaValve:
    """喷氨调节阀：单写者提交 + 优先级仲裁。"""

    SAFETY_PRIORITY = 80
    NOX_LOOP_PRIORITY = 50

    def __init__(self, min_rate_kg_h: float, max_rate_kg_h: float, staleness_s: float = 3.0) -> None:
        if min_rate_kg_h >= max_rate_kg_h:
            raise ValueError("喷氨率下限必须小于上限")
        self._min = float(min_rate_kg_h)
        self._max = float(max_rate_kg_h)
        self._staleness = float(staleness_s)
        self._pending: dict[str, ValveRequest] = {}
        self._sequence = 0
        self._committed = float(min_rate_kg_h)
        self._history: list[ValveOutcome] = []
        self._lock = threading.RLock()

    def clamp(self, rate: float) -> float:
        return max(self._min, min(self._max, float(rate)))

    def request(self, writer: str, rate_kg_h: float, priority: int) -> ValveRequest:
        """提交一次开度请求。"""

        with self._lock:
            self._sequence += 1
            request = ValveRequest(
                writer=writer,
                rate_kg_h=self.clamp(rate_kg_h),
                priority=int(priority),
                sequence=self._sequence,
                issued_at=time.monotonic(),
            )
            self._pending[writer] = request
            return request

    def resolve(self) -> ValveOutcome:
        """结算一次仲裁。"""

        moment = time.monotonic()
        with self._lock:
            live = [
                request
                for request in self._pending.values()
                if moment - request.issued_at <= self._staleness
            ]
            expired = [writer for writer, request in self._pending.items() if request not in live]
            for writer in expired:
                del self._pending[writer]
            if not live:
                outcome = ValveOutcome(winner="idle", rate_kg_h=self._committed, superseded=expired)
            else:
                live.sort(key=lambda item: (-item.priority, -item.sequence))
                winner = live[0]
                self._committed = winner.rate_kg_h
                outcome = ValveOutcome(
                    winner=winner.writer,
                    rate_kg_h=winner.rate_kg_h,
                    superseded=[item.writer for item in live[1:]] + expired,
                )
            self._history.append(outcome)
            if len(self._history) > 256:
                del self._history[: len(self._history) - 256]
            return outcome

    def committed_rate(self) -> float:
        with self._lock:
            return self._committed

    def history(self, limit: int = 20) -> list[dict]:
        with self._lock:
            return [outcome.as_dict() for outcome in self._history[-limit:]]

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "committed_rate_kg_h": round(self._committed, 3),
                "pending": [request.as_dict() for request in self._pending.values()],
                "min_rate_kg_h": self._min,
                "max_rate_kg_h": self._max,
            }
