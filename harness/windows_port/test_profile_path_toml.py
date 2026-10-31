"""Protect TOML profile paths generated from native Windows filenames."""
from pathlib import Path
import sys
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "harness/frame_capacity"))
import make_source


class GeneratedProfilePathTest(unittest.TestCase):
    def test_windows_path_round_trips_through_toml(self) -> None:
        source = Path(r"C:\work tree\日本語\frame_capacity.mp4")
        document = f"[source]\npath = {make_source.toml_string(source)}\n"
        self.assertEqual(tomllib.loads(document)["source"]["path"], str(source))


if __name__ == "__main__":
    unittest.main()
