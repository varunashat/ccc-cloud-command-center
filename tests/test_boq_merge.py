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


class TrafficManagerTest(unittest.TestCase):
    class FakeBook:
        currency = "USD"
        calls = 0

        def query(self, flt):
            base = {"type": "Consumption", "serviceName": "Traffic Manager", "armRegionName": "Global", "tierMinimumUnits": "0", "productName": "Traffic Manager"}
            return [base | {"meterName": "DNS Queries", "skuName": "Azure Endpoint", "unitOfMeasure": "1M", "retailPrice": 0.54, "meterId": "a"},
                    base | {"meterName": "DNS Queries", "skuName": "Azure Endpoint", "unitOfMeasure": "1M", "retailPrice": 0.375, "tierMinimumUnits": "1000", "meterId": "a"},
                    base | {"meterName": "DNS Queries", "skuName": "Non-Azure Endpoint", "unitOfMeasure": "1M", "retailPrice": 9.0, "meterId": "b"},
                    base | {"meterName": "Azure Endpoint Health Checks", "skuName": "Azure Endpoint", "unitOfMeasure": "1", "retailPrice": 0.36, "meterId": "c"},
                    base | {"meterName": "Azure Endpoint Fast Interval Health Check Add-ons", "skuName": "Azure Endpoint", "unitOfMeasure": "1", "retailPrice": 1.0, "meterId": "d"},
                    base | {"meterName": "Non-Azure Endpoint Health Checks", "skuName": "Non-Azure Endpoint", "unitOfMeasure": "1", "retailPrice": 0.54, "meterId": "e"},
                    base | {"meterName": "Non-Azure Endpoint Fast Interval Health Check Add-ons", "skuName": "Non-Azure Endpoint", "unitOfMeasure": "1", "retailPrice": 2.0, "meterId": "f"},
                    base | {"meterName": "Traffic View Data Points Processed", "skuName": "Traffic View", "unitOfMeasure": "1M", "retailPrice": 2.0, "meterId": "g"}]

    def test_priced_from_dns_queries_and_endpoint_checks(self):
        e = app.est()
        item = e.normalize_item({"type": "Traffic Manager", "name": "TM", "endpoints": 2, "queries_millions": 10}, DEFAULTS)
        line = e.PRICERS[item["type"]](self.FakeBook(), item)
        self.assertAlmostEqual(line["monthly"]["payg"], 10 * 0.54 + 2 * 0.36, places=2)

    def test_unavailable_price_is_a_clear_error(self):
        e = app.est()
        item = e.normalize_item({"type": "traffic_manager", "name": "TM"}, DEFAULTS)
        empty = type("B", (), {"query": lambda self, f: []})()
        with self.assertRaises(e.EstimateError):
            e.PRICERS["traffic_manager"](empty, item)

    def test_ai_intake_accepts_traffic_manager(self):
        e = app.est()
        items, _, _ = e.parse_ai_items('{"items":[{"type":"traffic_manager","name":"TM","endpoints":3}],"questions":[]}', DEFAULTS)
        self.assertEqual((items[0]["type"], items[0]["endpoints"]), ("traffic_manager", 3))


def rows(*specs):
    out = []
    for n, (product, sku, meter, region, price, unit) in enumerate(specs):
        out.append({"type": "Consumption", "serviceName": "Azure Bastion", "productName": product, "skuName": sku, "meterName": meter,
                    "armRegionName": region, "retailPrice": price, "unitOfMeasure": unit, "tierMinimumUnits": "0", "meterId": f"m{n}"})
    return out


class Book:
    currency = "USD"
    calls = 0

    def __init__(self, data):
        self.data = data

    def query(self, flt):
        return self.data


class GenericPricerTest(unittest.TestCase):
    def item(self, **kw):
        return app.est().normalize_item({"type": "azure_service", "name": "x", "service_name": "Azure Bastion", **kw}, DEFAULTS)

    def test_hourly_meter_times_instances_and_hours(self):
        e = app.est()
        data = rows(("Azure Bastion", "Standard", "Standard Gateway", "uaenorth", 0.29, "1 Hour"))
        line = e.PRICERS["azure_service"](Book(data), self.item(quantity=2, sku_name="Standard"))
        self.assertAlmostEqual(line["monthly"]["payg"], 0.29 * 730 * 2, places=2)

    def test_ambiguous_meter_is_refused_with_choices(self):
        e = app.est()
        data = rows(("Azure Bastion", "Basic", "Basic Gateway", "uaenorth", 0.19, "1 Hour"), ("Azure Bastion", "Standard", "Standard Gateway", "uaenorth", 0.29, "1 Hour"))
        with self.assertRaises(e.EstimateError) as cm:
            e.PRICERS["azure_service"](Book(data), self.item())
        self.assertIn("Basic Gateway", str(cm.exception))
        self.assertIn("Standard Gateway", str(cm.exception))

    def test_no_match_is_an_error_not_a_guess(self):
        e = app.est()
        with self.assertRaises(e.EstimateError):
            e.PRICERS["azure_service"](Book(rows(("Azure Bastion", "Basic", "Basic Gateway", "uaenorth", 0.19, "1 Hour"))), self.item(sku_name="Nope"))

    def test_zone_priced_service_uses_the_chosen_price_region(self):
        e = app.est()
        data = rows(("Azure Front Door", "Premium", "Premium Base Fees", "Zone 1", 330.0, "1/Month"))
        line = e.PRICERS["azure_service"](Book(data), self.item(price_region="Zone 1", quantity=1))
        self.assertAlmostEqual(line["monthly"]["payg"], 330.0)

    def test_catalog_search_keeps_region_global_and_zones(self):
        e = app.est()
        data = rows(("P", "S", "M1", "uaenorth", 1.0, "1"), ("P", "S", "M2", "Global", 2.0, "1"), ("P", "S", "M3", "Zone 1", 3.0, "1"),
                    ("P", "S", "M4", "westeurope", 4.0, "1"), ("P", "S", "M5", "US Gov", 5.0, "1"))
        book = type("B", (), {"currency": "USD", "calls": 0, "fetch": lambda self, url: {"Items": data}})()
        got = e.catalog_search(book, "Bastion", "uaenorth")
        self.assertEqual(sorted(m["meter"] for m in got["results"]), ["M1", "M2", "M3"])

    def test_intake_prompt_carries_existing_workload_and_allows_questions(self):
        e = app.est()
        text = e.ai_intake_prompt("add traffic manager", "uaenorth", "- vm web1 | region uaenorth | qty 4")
        self.assertIn("web1", text)
        self.assertIn("questions", text)
        self.assertIn("azure_service", text)


class TrafficManagerFullTest(unittest.TestCase):
    def test_all_calculator_parameters(self):
        e = app.est()
        item = e.normalize_item({"type": "traffic_manager", "name": "TM", "queries_millions": 100, "endpoints": 8, "fast_endpoints": 4,
                                 "external_endpoints": 8, "fast_external_endpoints": 5, "user_measurements_millions": 12, "data_points_millions": 6}, DEFAULTS)
        line = e.PRICERS["traffic_manager"](TrafficManagerTest.FakeBook(), item)
        # 100M*0.54 + 8*0.36 + 4*1 + 8*0.54 + 5*2 + 6M*2 = 54 + 2.88 + 4 + 4.32 + 10 + 12
        self.assertAlmostEqual(line["monthly"]["payg"], 87.2, places=2)

    def test_calculator_export_row_is_parsed(self):
        import openpyxl
        e = app.est()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.xlsx"
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.append(["Estimate"])
            ws.append(["Service category", "Service type", "Custom name", "Region", "Description", "Estimated Monthly USD"])
            ws.append(["Networking", "Traffic Manager", "", "Global", "100 million DNS queries, 8 Azure endpoints, 4 Fast Azure endpoints, 8 External endpoints, 5 Fast External endpoints, 12 million user measurements, 6 million data points processed", 90])
            wb.save(path)
            items, fmt, _ = e.read_input(path, DEFAULTS)
        tm = items[0]
        self.assertEqual((tm["type"], tm["queries_millions"], tm["endpoints"], tm["fast_endpoints"]), ("traffic_manager", 100, 8, 4))
        self.assertEqual((tm["external_endpoints"], tm["fast_external_endpoints"], tm["data_points_millions"], tm["user_measurements_millions"]), (8, 5, 6, 12))
