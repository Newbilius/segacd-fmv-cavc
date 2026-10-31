"""Prove the CLI holds real media and output locks before child execution."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import cavc_cli
import resource_tokens
from encode_config import load_profile


class CliLockTests(unittest.TestCase):
    def test_disc_child_cannot_reenter_parent_locks_without_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            profile_path = root / "Movie.toml"
            profile_path.write_text("""\
schema_version = 5

[source]
path = "missing/source.mp4"
fps = "24"
duration = "1"

[video]
width = 320
height = 224
fit = "pad"

[output]
directory = "tmpfs/movie/sim"
emit_decisions = true

[encoder]
cold_cap = 200

[palette]
algorithm = "mosaic-gm"
""", encoding="utf-8")
            profile = load_profile(profile_path)
            lock_root = root / "resources"
            script = f"""\
import json, sys
from pathlib import Path
sys.path.insert(0, {str(ROOT / 'tools')!r})
from resource_tokens import acquire_stems, ResourceBusyError
try:
    acquire_stems(json.loads(sys.argv[1]), wait=False, root=Path(sys.argv[2]))
except ResourceBusyError:
    raise SystemExit(75)
raise SystemExit(0)
"""

            def check_locks(_name, _command, *, env):
                child_env = dict(env)
                child_env.pop(resource_tokens.HELD_STEMS_ENV, None)
                child = subprocess.run(
                    [sys.executable, "-c", script,
                     json.dumps(profile.lock_stems), str(lock_root)],
                    cwd=ROOT,
                    env=child_env,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                self.assertEqual(
                    child.returncode, 75,
                    f"CLI child did not observe both parent locks: {child.stderr}",
                )
                return {"exit_code": 0}

            with patch.dict(os.environ, {
                "SEGACD_RESOURCE_ROOT": str(lock_root),
                resource_tokens.HELD_STEMS_ENV: "",
            }, clear=False), patch.object(cavc_cli, "run_stage", check_locks):
                self.assertEqual(
                    cavc_cli.main(["disc", str(profile_path), "--device", "cpu"]),
                    0,
                )


if __name__ == "__main__":
    unittest.main()
