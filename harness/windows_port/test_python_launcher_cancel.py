"""Verify Ctrl+Break through the PowerShell Python launcher stops descendants."""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import queue
import subprocess
import tempfile
import threading
import time
import unittest


ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / "tools" / "python.ps1"
POWERSHELL = Path(os.environ.get("SystemRoot", r"C:\Windows")) / (
    "System32/WindowsPowerShell/v1.0/powershell.exe"
)


def _process_running(pid: int) -> bool:
    """Query process completion without confusing PID 0 or access errors."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
    kernel32.WaitForSingleObject.restype = ctypes.c_uint32
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    kernel32.CloseHandle.restype = ctypes.c_int
    handle = kernel32.OpenProcess(0x00100000, 0, pid)  # SYNCHRONIZE
    if not handle:
        error = ctypes.get_last_error()
        if error == 87:  # ERROR_INVALID_PARAMETER: PID no longer exists.
            return False
        raise ctypes.WinError(error)
    try:
        return kernel32.WaitForSingleObject(handle, 0) != 0  # WAIT_OBJECT_0
    finally:
        kernel32.CloseHandle(handle)


@unittest.skipUnless(os.name == "nt", "PowerShell launcher cancellation is Windows-only")
class PythonLauncherCancellationTests(unittest.TestCase):
    def test_ctrl_break_stops_the_python_descendant_tree(self) -> None:
        self.assertTrue(POWERSHELL.is_file(), f"missing PowerShell: {POWERSHELL}")
        source = (
            "import subprocess, sys, time\n"
            "child = subprocess.Popen([sys.executable, '-c', "
            "'import time; time.sleep(600)'])\n"
            "print(f'CHILD_PID={child.pid}', flush=True)\n"
            "time.sleep(600)\n"
        )
        with tempfile.TemporaryDirectory(prefix="cavc-cancel-") as directory:
            worker = Path(directory) / "worker.py"
            worker.write_text(source, encoding="utf-8", newline="\n")
            process = subprocess.Popen(
                [
                    str(POWERSHELL), "-NoLogo", "-NoProfile",
                    "-ExecutionPolicy", "Bypass", "-File", str(LAUNCHER),
                    str(worker),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )
            child_pid = None
            try:
                lines: queue.Queue[str | None] = queue.Queue()

                def read_output() -> None:
                    assert process.stdout is not None
                    lines.put(process.stdout.readline())

                reader = threading.Thread(target=read_output, daemon=True)
                reader.start()
                try:
                    line = lines.get(timeout=20)
                except queue.Empty as error:
                    self.fail("launcher did not report the descendant PID")
                    raise AssertionError from error
                self.assertRegex(line, r"^CHILD_PID=\d+\s*$", line)
                child_pid = int(line.split("=", 1)[1])

                kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
                generate = kernel32.GenerateConsoleCtrlEvent
                generate.argtypes = (ctypes.c_uint32, ctypes.c_uint32)
                generate.restype = ctypes.c_int
                if not generate(1, process.pid):  # CTRL_BREAK_EVENT to owned group
                    raise ctypes.WinError(ctypes.get_last_error())

                try:
                    exit_code = process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    self.fail("PowerShell launcher did not exit after CTRL_BREAK_EVENT")
                self.assertNotEqual(exit_code, 0, "cancelled launcher reported success")

                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and _process_running(child_pid):
                    time.sleep(0.05)
                self.assertFalse(
                    _process_running(child_pid),
                    f"launcher cancellation left descendant process {child_pid} running",
                )
            finally:
                if process.poll() is None:
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=False,
                    )
                    process.wait(timeout=10)
                if child_pid is not None and _process_running(child_pid):
                    subprocess.run(
                        ["taskkill", "/PID", str(child_pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=False,
                    )
                if process.stdout is not None:
                    process.stdout.close()


if __name__ == "__main__":
    unittest.main()
