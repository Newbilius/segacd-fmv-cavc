from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import cavc_cli
import multipart


class CliStageObservationTests(unittest.TestCase):
    def test_child_progress_is_visible_and_retained_in_utf8_log(self) -> None:
        console = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            log_root = Path(directory)
            original_root = cavc_cli.ROOT
            cavc_cli.ROOT = log_root
            try:
                with redirect_stdout(console):
                    receipt = cavc_cli.run_stage(
                        "observation",
                        [sys.executable, "-c", "print('stage checkpoint')"],
                        env={},
                    )
            finally:
                cavc_cli.ROOT = original_root

            log = Path(receipt["log"])
            self.assertIn("stage checkpoint", console.getvalue())
            self.assertIn("stage checkpoint", log.read_text(encoding="utf-8"))
            self.assertEqual(receipt["exit_code"], 0)
            self.assertIsInstance(receipt["pid"], int)
            self.assertTrue(receipt["started_at"])
            self.assertTrue(receipt["ended_at"])

    def test_multipart_stage_receipt_has_process_identity_and_end_time(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "logs").mkdir()
            with patch.object(multipart, "ROOT", Path(directory)):
                receipt, lease = multipart.run_logged(
                    [sys.executable, "-c", "print('multipart checkpoint')"],
                    "receipt-test",
                )
                self.assertIsNone(lease)
                stored = Path(receipt["log"]).with_suffix(".json")
                saved = json.loads(stored.read_text(encoding="utf-8"))

            self.assertEqual(saved["exit_code"], 0)
            self.assertIsInstance(saved["pid"], int)
            self.assertTrue(saved["started_at"])
            self.assertTrue(saved["ended_at"])
            self.assertIn(
                "multipart checkpoint",
                Path(receipt["log"]).read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
