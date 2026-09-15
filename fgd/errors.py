"""平台统一异常类型。

烟气处理现场必须区分两类失败：一类是启动／停运时序被破坏（工程接线错误，
必须在装机阶段发现），另一类是联锁条件不满足（运行期可恢复，设备状态一
旦满足就能继续）。审计日志按异常类型分别落盘，巡检时才能一眼区分"顺序
错"还是"条件没到"。
"""

from __future__ import annotations


class FgdError(Exception):
    """所有平台异常的基类，携带出错的组件名。"""

    def __init__(self, message: str, *, component: str = "fgd") -> None:
        super().__init__(message)
        self.message = message
        self.component = component

    def as_record(self) -> dict:
        """转成审计日志可以直接落盘的一条记录。"""

        return {
            "type": type(self).__name__,
            "component": self.component,
            "message": self.message,
        }


class SequenceError(FgdError):
    """启动或停运顺序被破坏。"""


class InterlockError(FgdError):
    """联锁条件不满足，动作被拒绝。"""


class StorageError(FgdError):
    """持久化读写失败。"""


class ConfigError(FgdError):
    """配置缺失或取值非法。"""


class NamespaceError(FgdError):
    """命名空间重复注册或解析失败。"""
