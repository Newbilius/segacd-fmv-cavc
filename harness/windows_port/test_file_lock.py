"""Exercise lock contention across processes and release after owner death."""
import os
import errno
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))
from file_lock import close_locked, open_locked


class CrossProcessLockTest(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows msvcrt locking behavior")
    def test_permanent_lock_errors_are_not_reported_as_contention(self):
        with tempfile.TemporaryDirectory() as temporary:
            lock_path = Path(temporary) / "shared.lock"
            with patch(
                    "msvcrt.locking",
                    side_effect=OSError(errno.EIO, "simulated filesystem error")) as lock:
                with patch("file_lock.os.close", wraps=os.close) as close:
                    with self.assertRaises(OSError) as raised:
                        open_locked(lock_path)
            self.assertEqual(raised.exception.errno, errno.EIO)
            lock.assert_called_once()
            close.assert_called_once()

    def test_owner_exclusion_and_abnormal_exit_release(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lock_path = root / "shared.lock"
            ready_path = root / "ready"
            child = (
                "import sys,time; from pathlib import Path; "
                "sys.path.insert(0, sys.argv[3]); "
                "from file_lock import open_locked; "
                "fd=open_locked(Path(sys.argv[1])); "
                "Path(sys.argv[2]).touch(); time.sleep(60)"
            )
            env = dict(os.environ)
            env["PYTHONPATH"] = str(TOOLS) + os.pathsep + env.get("PYTHONPATH", "")
            owner = subprocess.Popen(
                [sys.executable, "-c", child, str(lock_path), str(ready_path), str(TOOLS)],
                cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            )
            try:
                deadline = time.monotonic() + 10
                while not ready_path.exists() and owner.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(ready_path.exists(), "child did not acquire its lock")
                self.assertIsNone(open_locked(lock_path, blocking=False))

                owner.kill()
                owner.wait(timeout=10)
                descriptor = open_locked(lock_path, blocking=False)
                self.assertIsNotNone(descriptor, "OS did not release the dead owner's lock")
                close_locked(descriptor)
            finally:
                if owner.poll() is None:
                    owner.kill()
                    owner.wait(timeout=10)
                if owner.stderr is not None:
                    owner.stderr.close()


if __name__ == "__main__":
    unittest.main()
