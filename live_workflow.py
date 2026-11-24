"""Read the signed-in user's selected Azure scope and prepare reviewable VM drafts."""
from uuid import UUID

from discovery import azure_discovery as azure
from assessment_hub import snapshot, inventory_csv
from deployment_plan import private_linux_vms


def run(body, call):
    tenant = str(UUID(str(body.get("tenant"))))
    subscriptions = azure.check_subscriptions(body.get("subscriptions"))
    for sub in subscriptions:
        account = call("GET", f"{azure.ARM}/subscriptions/{sub}?api-version=2022-12-01", tenant=tenant)
        if (str(account.get("tenantId")).lower() != tenant
                or str(account.get("subscriptionId")).lower() != sub or account.get("state") != "Enabled"):
            raise ValueError("Subscription unavailable in the selected directory.")
    if body["action"] == "live-plan":
        if len(subscriptions) != 1:
            raise ValueError("Select one subscription for the deployment plan.")
        fields = body.get("plan")
        keys = {"group", "region", "names", "size", "vnet", "subnet", "image_version"}
        if not isinstance(fields, dict) or keys != fields.keys():
            raise ValueError("Complete the deployment form.")
        return private_linux_vms(tenant=tenant, subscription=subscriptions[0], **fields)
    if body["action"] != "live-assessment":
        raise ValueError("Unknown live workflow.")
    query = "Resources | project id, name, type, location"
    results = azure.rg_query(call, subscriptions, query, tenant)
    report = snapshot(tenant, subscriptions, {"inventory": results})
    return {**report, "inventory_csv": inventory_csv(report)}

