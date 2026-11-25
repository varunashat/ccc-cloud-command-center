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


class CalculatorExportTest(unittest.TestCase):
    def build(self, path):
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Microsoft Azure Estimate X"])
        ws.append(["Service category", "Service type", "Custom name", "Region", "Description", "Estimated Monthly USD", "Estimated Monthly AED"])
        ws.append(["Compute", "Virtual Machines", "App VMs", "UAE North",
                   "4 E4as v5 (4 vCPUs, 32 GB RAM) x 730 Hours (Pay as you go), Windows (Licence included), OS Only", 1354.88, 4979.18])
        ws.append(["Storage", "Managed Disks", "", "UAE North", "Managed Disks, Premium SSD, LRS Redundancy, P15 Disk Type 5 Disks; Pay as you go", 207.36, 762.05])
        ws.append(["Networking", "Azure Front Door", "Front Door", None, "Azure Front Door Premium - Base instance included", 423.4, 1555.99])
        wb.save(path)

    def test_reads_calculator_export(self):
        e = app.est()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "Azure_Estimate.xlsx"
            self.build(path)
            items, fmt, warnings = e.read_input(path, {**DEFAULTS, "currency": "AED"})
        self.assertEqual(fmt, "azure_calculator_export")
        self.assertEqual([i["type"] for i in items], ["vm", "disk", "custom"])
        self.assertEqual((items[0]["quantity"], items[0]["os"]), (4, "windows"))
        self.assertEqual((items[1]["sku"], items[1]["quantity"]), ("P15", 5))
        self.assertAlmostEqual(items[2]["monthly"], 1555.99)   # AED column when pricing in AED
