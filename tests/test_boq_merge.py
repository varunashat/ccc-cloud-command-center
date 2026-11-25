import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import app

DEFAULTS = {"region": "uaenorth", "hours": 730, "currency": "USD", "disk_tier": "premium", "ahub": False, "include_off": False}


class MergeTest(unittest.TestCase):
    def test_added_service_goes_after_uploaded_lines(self):
        e = app.est()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "estimator" / "inputs").mkdir(parents=True)
            base = "ab12cd34-boq.json"
            (root / "estimator" / "inputs" / base).write_text(json.dumps({"items": [
                {"type": "vm", "name": "web1", "region": "uaenorth", "quantity": 2, "vcpu": 4, "ram_gb": 16, "os": "windows"}]}))
            extra = [e.normalize_item({"type": "blob", "name": "archive", "region": "uaenorth", "quantity": 1, "tb": 5}, DEFAULTS)]
            with mock.patch.object(app, "ROOT", root):
                items, warnings = app.merge_base(e, base, DEFAULTS, extra, [])
        self.assertEqual([i["name"] for i in items], ["web1", "archive"])

    def test_missing_base_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(app, "ROOT", Path(tmp)):
            with self.assertRaises(ValueError):
                app.merge_base(app.est(), "ab12cd34-gone.xlsx", DEFAULTS, [], [])

    def test_bad_name_is_refused(self):
        with self.assertRaises(ValueError):
            app.merge_base(app.est(), "../../etc/passwd", DEFAULTS, [], [])


if __name__ == "__main__":
    unittest.main()
