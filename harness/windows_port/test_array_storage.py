"""Check that disk-backed arrays survive live views and clean up on Windows."""
import gc
import os
from pathlib import Path
import tempfile
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
import sys
sys.path.insert(0, str(ROOT / "tools"))
from array_storage import ArrayStorage


class WindowsArrayStorageTest(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "tests Windows open-mapping deletion semantics")
    def test_mapping_view_outlives_owner_then_removes_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            scratch = Path(temporary) / "scratch"
            scratch.mkdir()
            old_storage = os.environ.get("CAVC_ARRAY_STORAGE")
            old_scratch = os.environ.get("CAVC_SCRATCH_ROOT")
            os.environ["CAVC_ARRAY_STORAGE"] = "disk"
            os.environ["CAVC_SCRATCH_ROOT"] = str(scratch)
            try:
                storage = ArrayStorage()
                with storage:
                    mapped = storage.empty((16, 8), np.uint8)
                    path = Path(mapped.filename)
                    mapped[:] = np.arange(128, dtype=np.uint8).reshape(16, 8)
                    view = mapped[4:]
                    del mapped
                np.testing.assert_array_equal(
                    view, np.arange(128, dtype=np.uint8).reshape(16, 8)[4:])
                self.assertTrue(path.is_file(), "live view lost its mapped backing file")

                del view
                gc.collect()
                self.assertFalse(path.exists(), "released mapping left its array file")
                self.assertFalse(path.parent.exists(), "released mapping left its array directory")
            finally:
                if old_storage is None:
                    os.environ.pop("CAVC_ARRAY_STORAGE", None)
                else:
                    os.environ["CAVC_ARRAY_STORAGE"] = old_storage
                if old_scratch is None:
                    os.environ.pop("CAVC_SCRATCH_ROOT", None)
                else:
                    os.environ["CAVC_SCRATCH_ROOT"] = old_scratch


if __name__ == "__main__":
    unittest.main()
