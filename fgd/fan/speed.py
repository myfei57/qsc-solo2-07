"""引风机转速仲裁。

锅炉负压调节和脱硫压降调节共用同一台引风机。两个回路各自按自己的目标算
出请求转速，由仲裁器按优先级选出唯一提交值——没有仲裁时两个回路会把转速
来回拉扯，炉膛负压跟着摆动。仲裁是并发安全的：两个调节回路在不同线程里
同时提交请求，最终只会有一个转速被提交。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass(frozen=True)
class SpeedRequest:
    """一次转速请求。"""

    loop: str
    rpm: float
    priority: int
    sequence: int
    issued_at: float
    reason: str = ""

    def as_dict(self) -> dict:
        return {
            "loop": self.loop,
            "rpm": round(self.rpm, 2),
            "priority": self.priority,
            "sequence": self.sequence,
            "reason": self.reason,
        }


@dataclass
class ArbitrationOutcome:
    """一次仲裁结果。"""

    winner: str
    rpm: float
    rejected: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"winner": self.winner, "rpm": round(self.rpm, 2), "rejected": list(self.rejected)}


class SpeedArbiter:
    """按优先级仲裁多个调节回路的转速请求。"""

    def __init__(self, min_rpm: float, max_rpm: float, staleness_s: float = 5.0) -> None:
        if min_rpm >= max_rpm:
            raise ValueError("最小转速必须小于最大转速")
        self._min = float(min_rpm)
        self._max = float(max_rpm)
        self._staleness = float(staleness_s)
        self._pending: dict[str, SpeedRequest] = {}
        self._sequence = 0
        self._decisions: list[ArbitrationOutcome] = []
        self._committed = float(min_rpm)
        self._lock = threading.RLock()

    def clamp(self, rpm: float) -> float:
        return max(self._min, min(self._max, float(rpm)))

    def request(self, loop: str, rpm: float, priority: int, reason: str = "") -> SpeedRequest:
        """提交一次转速请求。"""

        with self._lock:
            self._sequence += 1
            request = SpeedRequest(
                loop=loop,
                rpm=self.clamp(rpm),
                priority=int(priority),
                sequence=self._sequence,
                issued_at=time.monotonic(),
                reason=reason,
            )
            self._pending[loop] = request
            return request

    def _live_requests(self, now: float | None = None) -> list[SpeedRequest]:
        moment = time.monotonic() if now is None else now
        return [
            request
            for request in self._pending.values()
            if moment - request.issued_at <= self._staleness
        ]

    def resolve(self) -> ArbitrationOutcome:
        """结算一次仲裁，把最高优先级请求提交为唯一转速。"""

        with self._lock:
            live = self._live_requests()
            stale = [
                loop for loop, request in self._pending.items() if request not in live
            ]
            for loop in stale:
                del self._pending[loop]
            if not live:
                outcome = ArbitrationOutcome(winner="idle", rpm=self._committed, rejected=stale)
            else:
                live.sort(key=lambda item: (-item.priority, -item.sequence))
                winner = live[0]
                self._committed = winner.rpm
                outcome = ArbitrationOutcome(
                    winner=winner.loop,
                    rpm=winner.rpm,
                    rejected=[item.loop for item in live[1:]] + stale,
                )
            self._decisions.append(outcome)
            if len(self._decisions) > 256:
                del self._decisions[: len(self._decisions) - 256]
            return outcome

    def committed_rpm(self) -> float:
        with self._lock:
            return self._committed

    def decisions(self, limit: int = 20) -> list[dict]:
        with self._lock:
            return [outcome.as_dict() for outcome in self._decisions[-limit:]]

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "committed_rpm": round(self._committed, 2),
                "pending": [request.as_dict() for request in self._pending.values()],
                "min_rpm": self._min,
                "max_rpm": self._max,
            }
