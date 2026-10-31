from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import parallel_run
from encode_config import EncodeProfile, load_profile
import resource_tokens
import multipart


def write_profile(path: Path, *, source: str, width: int = 320) -> None:
    path.write_text(f"""\
schema_version = 5

[source]
path = "{source}"
fps = "30"
duration = "8"

[video]
width = {width}
height = 224
fit = "pad"

[output]
directory = "tmpfs/{Path(source).stem}_H40_{width}x224_adpcm22/sim"
emit_decisions = true

[encoder]
cold_cap = 200

[palette]
algorithm = "mosaic-gm"
""", encoding="utf-8")


class ParallelRunTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows paths are case-insensitive by default")
    def test_case_only_profile_stems_cannot_share_output_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            upper_path = root / "first" / "Clip.toml"
            lower_path = root / "second" / "clip.toml"
            upper_path.parent.mkdir()
            lower_path.parent.mkdir()
            write_profile(upper_path, source="assets/alpha.mp4")
            write_profile(lower_path, source="assets/beta.mp4")
            upper = load_profile(upper_path)
            lower = load_profile(lower_path)
            self.assertNotEqual(upper.sim_stem, lower.sim_stem)
            self.assertEqual(upper.output_lock_stem, lower.output_lock_stem)

            with patch.dict(os.environ, {
                "SEGACD_RESOURCE_ROOT": str(root / "locks"),
                "SEGACD_CPU_TOKENS": "2",
            }, clear=False):
                with resource_tokens.acquire_stems(upper.lock_stems):
                    with self.assertRaises(resource_tokens.ResourceBusyError):
                        resource_tokens.acquire_stems(lower.lock_stems)
                    # A busy second output lock must not leak its source lock.
                    source_lease = resource_tokens.acquire_stem(
                        lower.sim_stem, wait=False)
                    source_lease.release()

            with self.assertRaisesRegex(
                parallel_run.ParallelRunError, "case-normalized output path"):
                parallel_run.validate_distinct_stems([upper, lower])

    @unittest.skipUnless(os.name == "nt", "checks Windows generated filename bounds")
    def test_windows_profile_stem_leaves_room_for_disc_suffixes(self) -> None:
        profile = EncodeProfile(Path("x" * 241 + ".toml"), {}, "")
        with self.assertRaisesRegex(ValueError, "maximum is 240"):
            _ = profile.artifact_stem
        for reserved in ("CON", "nul", "COM1", "lpt9"):
            profile = EncodeProfile(Path(reserved + ".toml"), {}, "")
            with self.subTest(stem=reserved), self.assertRaisesRegex(
                ValueError, "reserved Windows artifact filename stem"):
                _ = profile.artifact_stem

    @unittest.skipUnless(os.name == "nt", "checks Windows multipart filename bounds")
    def test_windows_multipart_name_reserves_part_suffix(self) -> None:
        multipart.validate_windows_part_stem("x" * 232)
        with self.assertRaisesRegex(ValueError, "generated part profiles"):
            multipart.validate_windows_part_stem("x" * 233)

    def test_logged_child_receipt_includes_process_identity_and_times(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "parallel.log"
            with path.open("w+", encoding="utf-8") as log:
                status = parallel_run._run_logged(
                    [sys.executable, "-c", "print('child output')"],
                    env=dict(os.environ),
                    log=log,
                )
            text = path.read_text(encoding="utf-8")
            self.assertEqual(status, 0)
            self.assertIn("child output", text)
            self.assertIn("# pid=", text)
            self.assertIn("started_at=", text)
            self.assertIn("ended_at=", text)
            self.assertIn("exit_code=0", text)

    def test_rejects_duplicate_video_stems(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "first.toml"
            second = root / "second.toml"
            write_profile(first, source="assets/same.mp4")
            write_profile(second, source="assets/same.mp4")
            with self.assertRaises(parallel_run.ParallelRunError):
                parallel_run.validate_distinct_stems([
                    load_profile(first), load_profile(second)])

    def test_stage_commands_stop_at_requested_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            profile_path = Path(tmp) / "movie.toml"
            write_profile(profile_path, source="assets/movie.mp4")
            profile = load_profile(profile_path)
            commands = parallel_run.stage_commands(
                profile,
                through="record",
                use_gpu=False,
                record_seconds=41,
            )
            self.assertEqual(
                [stage for stage, _command in commands],
                ["sim", "disc", "record"],
            )
            record = commands[-1][1]
            self.assertIn("320x224", record)
            self.assertIn("41", record)
            self.assertNotIn("--gpu", commands[0][1])

    def test_cancel_stops_grandchild_process(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary) / "grandchild.pid"
            child = (
                "import subprocess,sys,time; "
                "grandchild=subprocess.Popen([sys.executable,'-c',"
                "'import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(60)']); "
                "open(sys.argv[1],'w').write(str(grandchild.pid)); time.sleep(60)"
            )
            parallel_run._CANCEL.clear()
            process = parallel_run._spawn_owned(
                [sys.executable, "-c", child, str(marker)], cwd=Path.cwd(),
                env=dict(os.environ), stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                deadline = time.monotonic() + 10
                while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(marker.exists(), "child did not start its grandchild")
                grandchild_pid = int(marker.read_text())
                parallel_run._CANCEL.set()
                parallel_run._terminate_active_processes()
                process.wait(timeout=10)

                def grandchild_running() -> bool:
                    if sys.platform.startswith("linux"):
                        stat = Path(f"/proc/{grandchild_pid}/stat")
                        try:
                            state = stat.read_text().rsplit(")", 1)[1].strip()[0]
                        except FileNotFoundError:
                            return False
                        return state not in ("Z", "X")
                    return parallel_run.tmpfs_workspace._pid_alive(grandchild_pid)

                deadline = time.monotonic() + 10
                while grandchild_running() and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertFalse(
                    grandchild_running(),
                    "task-tree cancellation left the grandchild running",
                )
            finally:
                if process.poll() is None:
                    parallel_run._terminate_tree(process)
                    process.wait(timeout=10)
                parallel_run._release_owned(process)
                parallel_run._CANCEL.clear()


if __name__ == "__main__":
    unittest.main()
