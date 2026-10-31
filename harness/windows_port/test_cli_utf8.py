"""Ensure direct native CLI launches pass UTF-8 file defaults to child stages."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


@unittest.skipUnless(os.name == "nt", "checks Windows locale text defaults")
class CliUtf8EnvironmentTest(unittest.TestCase):
    def test_direct_cli_child_writes_non_ascii_with_utf8_default(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "report.txt"
            script = """
import os, subprocess, sys
import cavc_cli
assert os.environ.get('PYTHONUTF8') == '1'
subprocess.run([
    sys.executable, '-c',
    'from pathlib import Path; import sys; Path(sys.argv[1]).write_text(\"日本語\")',
    sys.argv[1],
], env=os.environ.copy(), check=True)
"""
            environment = os.environ.copy()
            environment.pop("PYTHONUTF8", None)
            subprocess.run(
                [sys.executable, "-c", script, str(target)],
                cwd=Path(__file__).resolve().parents[2],
                env=environment,
                check=True,
            )
            self.assertEqual(target.read_bytes().decode("utf-8"), "日本語")


if __name__ == "__main__":
    unittest.main()
