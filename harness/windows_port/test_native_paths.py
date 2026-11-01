"""Exercise native m68k tools with spaces and Unicode in file paths."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import build_disc


@unittest.skipUnless(os.name == "nt", "native Windows toolchain path test")
class NativeToolPathTest(unittest.TestCase):
    def test_extension_assembles_from_unicode_paths(self) -> None:
        try:
            build_disc.tool("as")
            build_disc.tool("ld")
        except FileNotFoundError as exc:
            self.skipTest(str(exc))

        suffix = chr(0x65E5) + chr(0x672C)
        with tempfile.TemporaryDirectory(prefix="cavc path ") as temporary:
            workspace = Path(temporary) / f"native-{suffix}"
            workspace.mkdir()
            source = workspace / "movieplay_sp_ext.s"
            linker = workspace / "sp_ext.ld"
            shutil.copyfile(build_disc.ROOT / "boot/movieplay_sp_ext.s", source)
            shutil.copyfile(build_disc.ROOT / "cfg/sp_ext.ld", linker)
            obj = workspace / "movieplay_sp_ext.o"
            binary = workspace / "movieplay_sp_ext.bin"

            with patch.object(build_disc, "_short_windows_native_path",
                              side_effect=lambda path: os.fspath(path)):
                build_disc._assemble(source, obj, [workspace])
                build_disc._link(obj, linker, binary)

            self.assertEqual(binary.stat().st_size, 1108)


if __name__ == "__main__":
    unittest.main()
