"""Small cross-platform file lock used by runtime coordination."""
from __future__ import annotations

import errno
import os
from pathlib import Path
import time


def open_locked(path: Path, *, blocking: bool = True) -> int | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        if os.name == "nt":
            import msvcrt
            contention_errors = {
                errno.EACCES,
                errno.EAGAIN,
                errno.EDEADLK,
                errno.EWOULDBLOCK,
            }
            while True:
                try:
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    # Poll in user space so blocking acquisition remains
                    # interruptible and permanent filesystem errors are not
                    # hidden inside msvcrt's long LK_LOCK retry period.
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                    return descriptor
                except OSError as exc:
                    if exc.errno not in contention_errors:
                        raise
                    if not blocking:
                        os.close(descriptor)
                        return None
                    time.sleep(0.05)
        else:
            import fcntl
            flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
            try:
                fcntl.flock(descriptor, flags)
                return descriptor
            except BlockingIOError:
                os.close(descriptor)
                return None
    except BaseException:
        os.close(descriptor)
        raise


def close_locked(descriptor: int) -> None:
    if os.name == "nt":
        import msvcrt
        os.lseek(descriptor, 0, os.SEEK_SET)
        try:
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        finally:
            os.close(descriptor)
    else:
        os.close(descriptor)
