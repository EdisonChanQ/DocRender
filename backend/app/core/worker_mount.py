"""流水线 worker 的后端挂载入口。

后端进程（uvicorn）启动时由 lifespan 调用 `mount()`，把常驻 worker 挂到
后台线程 —— 做到「后端起来 = 队列自动被消费」，无需另开终端跑 worker.py。

## 三个坑（都已处理）

1. **uvicorn --reload**：由 reloader 父进程 spawn 真正的 serve 子进程。
   父进程只监视文件变化、**不执行 lifespan**（已读 uvicorn basereload 源码确认），
   所以默认只有子进程挂载，不会重复。

2. **uvicorn --workers N / 手滑起了两个后端**：每个进程都会执行 lifespan。
   本模块用**跨进程文件锁**（Windows `msvcrt.locking`，进程退出自动释放，
   无陈旧锁问题）保证全机只有一个消费线程。抢不到锁的进程照常提供 API。
   注：即便漏了这层，多消费者也是**安全**的（claim 用 READPAST+ROWLOCK，
   抢不到就空转），这一层只是避免无谓的空转线程。

3. **数据库未配置 / 路径表缺失**：worker 会抛异常。挂载层必须全吞，
   否则后端起不来 —— 后端提供 API 与 worker 消费队列是可独立降级的。

## 关闭方式

`.env` 设 `WORKER_ENABLED=false` 关闭自动挂载（例如想让 worker 单独部署时）。
手动前台运行 `python worker.py run` 始终可用，不受此开关影响。
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

from app.config import BASE_DIR, settings

log = logging.getLogger("worker.mount")

LOCK_PATH = BASE_DIR / "storage" / ".pipeline_worker.lock"
LOCK_TIMEOUT_SECONDS = 0.0  # 非阻塞：抢不到就让位

_stop_event: threading.Event | None = None
_lock_handle = None  # 持有锁的文件句柄，进程存活期间不能关闭


def _try_lock() -> bool:
    """尝试跨进程独占锁。成功返回 True，被占用返回 False。

    Windows 用 msvcrt.locking（进程退出由 OS 自动释放，无陈旧锁）；
    其它平台退化为「不锁」（多消费者本身安全）。
    """
    global _lock_handle
    try:
        import msvcrt
    except ImportError:  # 非 Windows：不做跨进程防重
        return True

    try:
        LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        # 必须是 r+b（可读可写、不追加）——用 a+b 追加模式时 seek+truncate+write
        # 在 Windows 上行为异常，会写出空文件（已踩坑）。
        if not LOCK_PATH.exists():
            LOCK_PATH.touch()
        handle = open(LOCK_PATH, "r+b")
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        try:
            handle.close()
        except Exception:  # noqa: BLE001
            pass
        return False
    except Exception as exc:  # noqa: BLE001 - 锁机制异常不该阻断启动
        log.warning("worker 文件锁不可用（%s），本次不做跨进程防重", exc)
        return True

    _lock_handle = handle  # 必须持有，关闭即释放锁
    try:
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()).encode())
        handle.flush()
    except Exception:  # noqa: BLE001
        pass
    return True


def _release_lock() -> None:
    global _lock_handle
    if _lock_handle is None:
        return
    try:
        import msvcrt

        _lock_handle.seek(0)
        msvcrt.locking(_lock_handle.fileno(), msvcrt.LK_UNLCK, 1)
    except Exception:  # noqa: BLE001
        pass
    try:
        _lock_handle.close()
    except Exception:  # noqa: BLE001
        pass
    _lock_handle = None


def mount() -> bool:
    """把 worker 挂到当前进程的后台线程，返回是否真正启动。

    幂等，且**绝不抛异常** —— 后端启动流程不能被 worker 的任何问题打断。
    """
    global _stop_event

    if not settings.worker_enabled:
        log.info("worker 自动挂载已关闭（WORKER_ENABLED=false）；需自行运行 python worker.py run")
        return False

    if _stop_event is not None:
        log.debug("worker 已在本进程挂载，跳过")
        return True

    if not _try_lock():
        log.info("worker 已由其它进程挂载（%s），本进程只提供 API", LOCK_PATH.name)
        return False

    try:
        import worker as worker_module

        worker_module.IDLE_SLEEP_SECONDS = settings.worker_idle_seconds
        _stop_event = threading.Event()
        worker_module.start_background(_stop_event)
    except Exception as exc:  # noqa: BLE001 - 挂载失败绝不能影响后端启动
        log.warning("worker 自动挂载失败（后端照常提供 API，可手动 python worker.py run）：%s", exc)
        _release_lock()
        _stop_event = None
        return False

    log.info(
        "worker 已随后端自动挂载（pid=%s，空闲轮询 %.0fs）",
        os.getpid(),
        settings.worker_idle_seconds,
    )
    return True


def unmount() -> None:
    """请求后台 worker 停止：置位 stop_event，等当前任务跑完自然退出。"""
    global _stop_event
    if _stop_event is None:
        return
    _stop_event.set()
    _stop_event = None
    _release_lock()
    log.info("已通知后台 worker 停止")
