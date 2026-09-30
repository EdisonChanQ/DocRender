"""数据库并发冲突重试工具（死锁 / 锁超时）。

SQL Server 在高并发抢任务（claim / 回收 / 聚合）时可能抛出：
- 1205 死锁（Transaction ... was deadlocked ... chosen as the deadlock victim）
- 1222 锁请求超时（Lock request time out period exceeded）

这两类错误是**并发竞态的必然产物**，正确应对是「重跑一次」而非报错给调用方。
本模块提供 retry_on_lock_conflict 装饰器：捕获上述错误码，短退避后自动重试，
重试耗尽才把异常原样抛出。

注意：重试要求被装饰函数自身是「事务自包含」的（每次调用内部独立 engine.begin()/commit），
这样重试才安全——claim/heartbeat/complete 等 service 均满足该前提。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, TypeVar

# 退避间隔（秒），重试次数与列表长度一致
_BACKOFF_SECONDS = (0.05, 0.2, 0.5)

F = TypeVar("F", bound=Callable[..., Any])


def _is_lock_conflict(exc: BaseException) -> bool:
    """判断异常是否为死锁 / 锁超时（SQL Server 1205 / 1222）。

    沿异常链（cause/context）以及 SQLAlchemy 的 .orig 包装层层向下查找，
    只要任一层的错误文本包含 1205 / 1222 / deadlock 关键字即判定为锁冲突。
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))

        # 1) 检查 .orig（SQLAlchemy 包裹的底层驱动异常，如 pyodbc.Error）
        orig = getattr(current, "orig", None)
        if orig is not None and _has_lock_signal(orig):
            return True

        # 2) 检查当前异常自身的 args / 文本
        if _has_lock_signal(current):
            return True

        # 3) 沿异常链下探
        nxt = current.__cause__ or current.__context__
        if nxt is None and orig is not None:
            nxt = orig
        current = nxt

    return False


def _has_lock_signal(exc: BaseException) -> bool:
    """检查单个异常的 args / 字符串里是否带锁冲突信号。"""
    args = getattr(exc, "args", None)
    if isinstance(args, (tuple, list)):
        for part in args:
            if isinstance(part, str) and _lock_text(part):
                return True
    text = str(exc)
    return _lock_text(text)


def _lock_text(s: str) -> bool:
    low = s.lower()
    if "(1205)" in low or "(1222)" in low or "deadlock" in low or "lock request time out" in low:
        return True
    # 08001 预登录/握手类连接失败（并发涌开连接时服务器重置 TCP，压测实测 10054）。
    # 属瞬时故障，重试安全；登录超时（ConnectionTimeout）由 pyodbc 归入本类一并兜底。
    if "08001" in low and ("握手" in s or "handshake" in low or "10054" in low or "连接" in s or "connect" in low):
        return True
    return False


def retry_on_lock_conflict(func: F) -> F:
    """装饰器：死锁 / 锁超时自动重试（最多 len(_BACKOFF_SECONDS) 次）。"""

    def wrapper(*args: Any, **kwargs: Any) -> Any:
        for attempt in range(len(_BACKOFF_SECONDS) + 1):
            try:
                return func(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - 仅对锁冲突重试，其余原样抛出
                if not _is_lock_conflict(exc) or attempt >= len(_BACKOFF_SECONDS):
                    raise
                time.sleep(_BACKOFF_SECONDS[attempt])
        raise RuntimeError("unreachable")  # pragma: no cover

    wrapper.__name__ = getattr(func, "__name__", "wrapped")
    wrapper.__doc__ = func.__doc__
    return wrapper  # type: ignore[return-value]
