"""Build a scoped assessment snapshot from the existing discovery response."""
import csv
import io
from datetime import datetime, timezone

from discovery.azure_discovery import check_subscriptions, check_tenant, summarize


def snapshot(tenant, subscriptions, result, *, synthetic=False):
    tenant = check_tenant(tenant)
    subscriptions = sorted(check_subscriptions(subscriptions))
    allowed = set(subscriptions)
    inventory = result.get("inventory")
    if inventory is None:
        inventory = []
        status = "unavailable"
    else:
        status = "empty" if not inventory else "available"
    if any(str(failure).startswith("Inventory:") for failure in result.get("failures", [])):
        status = "partial" if inventory else "unavailable"
    resources = []
    for row in inventory:
        resource_id = row.get("id", "").lower().split("/")
        if len(resource_id) < 3 or resource_id[1] != "subscriptions" or resource_id[2] not in allowed:
            raise ValueError("Inventory contains a resource outside the selected subscriptions.")
        resources.append({**{field: row.get(field, "") for field in ("id", "name", "type", "location")},
                          "subscriptionId": resource_id[2]})
    return {
        "tenant": tenant, "subscriptions": subscriptions,
        "captured_at": result.get("collected_utc") or datetime.now(timezone.utc).isoformat(),
        "synthetic": synthetic, "inventory_status": status, "inventory_summary": summarize(resources),
        "resources": resources, "estimated_savings": None,
        "evidence_gaps": ["Backup and DR coverage", "Observed traffic", "Advisor savings and utilization"],
    }


def inventory_csv(run):
    writer_buffer = io.StringIO(newline="")
    writer = csv.writer(writer_buffer)
    writer.writerow(["Tenant", "Subscriptions", "Captured at", "Resource ID", "Name", "Type", "Region"])
    def cell(value):
        value = str(value)
        return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")) else value
    for row in run["resources"]:
        writer.writerow([cell(value) for value in [
            run["tenant"], ", ".join(run["subscriptions"]), run["captured_at"],
            row["id"], row["name"], row["type"], row["location"],
        ]])
    return writer_buffer.getvalue()


def sample():
    tenant = "00000000-0000-4000-8000-000000000001"
    subscription = "00000000-0000-4000-8000-000000000002"
    prefix = f"/subscriptions/{subscription}/resourceGroups/rg-ccc-sample/providers/"
    resources = [
        {"name": "sample-vm", "type": "microsoft.compute/virtualmachines", "location": "uaenorth",
         "id": prefix + "Microsoft.Compute/virtualMachines/sample-vm"},
        {"name": "samplestorage", "type": "microsoft.storage/storageaccounts", "location": "uaenorth",
         "id": prefix + "Microsoft.Storage/storageAccounts/samplestorage"},
    ]
    run = snapshot(tenant, [subscription], {"inventory": resources}, synthetic=True)
    return {**run, "inventory_csv": inventory_csv(run)}
