from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


@contextmanager
def runtime_file_lock(path: Path) -> Iterator[None]:
    """Serialize local runtime JSON operations across backend processes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        if os.name == "nt":
            import msvcrt

            while True:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.05)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class SingleInstanceError(RuntimeError):
    """同一个 runtime 目录已经有后端进程在占用。"""


def acquire_single_instance_lock(path: Path, *, label: str = "后端") -> Any:
    """非阻塞地占住单实例锁，成功时返回**必须在进程生命周期内一直持有**的句柄。

    与 runtime_file_lock 的区别：那个是临界区锁，抢不到就排队等；单实例锁要求整个
    进程存活期间持有，且抢不到必须立刻失败 —— 两个后端同时读写同一个 runtime 目录
    会各自持有内存副本互相覆盖，导致 catalog 条目凭空消失、已删数据复活，这种事故
    宁愿起不来也不能静默双开。

    进程正常退出或被强杀时由操作系统自动释放，不会留下需要手工清理的死锁文件。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        handle.close()
        raise SingleInstanceError(
            f"{label}已经在运行（{path} 被另一个进程占用）。\n"
            f"同一个 runtime 目录不能同时跑两个后端：两个进程会互相覆盖 "
            f"catalog/workspaces，导致条目消失或已删数据复活。\n"
            f"请先找到并关闭已有实例：\n"
            f"    netstat -ano | findstr :8797\n"
            f"确需并行（例如临时测试）可设置 TENDER_AGENT_ALLOW_MULTI_INSTANCE=1 跳过本检查。"
        ) from error
    return handle
