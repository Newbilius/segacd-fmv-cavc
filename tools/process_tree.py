"""Start and stop task-owned subprocess trees across supported hosts."""
from __future__ import annotations

import os
import signal
import subprocess
import time


_POSIX_TERMINATE_GRACE_SECONDS = 2.0


def popen(command, **options) -> subprocess.Popen:
    """Launch a child in an isolated process group/session for cancellation."""
    if os.name == "nt":
        options["creationflags"] = (
            options.get("creationflags", 0) | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        options["start_new_session"] = True
    return subprocess.Popen(command, **options)


def terminate(process: subprocess.Popen) -> None:
    """Stop the process and descendants started in its owned group."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if process.poll() is None:
            process.kill()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            process.wait()
            return

        deadline = time.monotonic() + _POSIX_TERMINATE_GRACE_SECONDS
        while True:
            # poll() reaps the direct child so its zombie does not make the
            # process group appear live after all descendants have exited.
            process.poll()
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                break
            except PermissionError:
                pass
            if time.monotonic() >= deadline:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                break
            time.sleep(0.05)

        if process.poll() is None:
            process.wait()
