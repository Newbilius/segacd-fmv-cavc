"""Exercise Sega CD ISO creation using the independent multipart ISO reader."""
from pathlib import Path
import tempfile
import unittest

import sys
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import iso9660
from multipart_build import verify_iso


class NativeIsoWriterTest(unittest.TestCase):
    def test_system_area_and_staged_payloads_survive_iso_round_trip(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            boot = root / "boot.bin"
            staged = root / "disc"
            staged.mkdir()
            iso = root / "movie.iso"
            boot_bytes = bytes(range(256)) * 128
            boot.write_bytes(boot_bytes)
            (staged / "HEADER.DAT").write_bytes(b"header" * 1000)
            (staged / "BODY.DAT").write_bytes(bytes(range(251)) * 1900)
            (staged / "README.TXT").write_bytes(b"CAVC test disc\n")

            iso9660.write_iso(iso, boot, staged, "SCFMV_DLT")
            verify_iso(iso, staged)

            with iso.open("rb") as image:
                self.assertEqual(image.read(16 * iso9660.SECTOR), boot_bytes)
                image.seek(16 * iso9660.SECTOR)
                pvd = image.read(iso9660.SECTOR)
                self.assertEqual(pvd[:7], b"\x01CD001\x01")
                self.assertEqual(int.from_bytes(pvd[132:136], "little"), 10)
                little_path_lba = int.from_bytes(pvd[140:144], "little")
                big_path_lba = int.from_bytes(pvd[148:152], "big")
                image.seek(little_path_lba * iso9660.SECTOR)
                self.assertEqual(image.read(10), b"\x01\0\x14\0\0\0\x01\0\0\0")
                image.seek(big_path_lba * iso9660.SECTOR)
                self.assertEqual(image.read(10), b"\x01\0\0\0\0\x14\0\x01\0\0")
            self.assertEqual(iso.stat().st_size % iso9660.SECTOR, 0)


if __name__ == "__main__":
    unittest.main()
