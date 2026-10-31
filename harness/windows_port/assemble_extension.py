"""Assemble and link the real Sub player extension with native Windows tools."""
from __future__ import annotations

from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import build_disc
import sp_extension


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="cavc-m68k-extension-") as temporary:
        output = Path(temporary)
        obj = output / "movieplay_sp_ext.o"
        binary = output / "movieplay_sp_ext.bin"
        constants = output / "sp_extension.inc"
        build_disc._assemble(ROOT / "boot/movieplay_sp_ext.s", obj,
                             [ROOT / "boot"])
        build_disc._link(obj, ROOT / "cfg/sp_ext.ld", binary)
        sp_extension.generate(binary, constants)
        print(f"native Sub extension assembled and linked: {binary.stat().st_size} bytes")


if __name__ == "__main__":
    main()
