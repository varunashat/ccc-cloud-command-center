"""Plan private Linux VMs and export reviewable ARM without executing a deployment."""
import hashlib
import json
import re

from discovery.azure_discovery import check_subscriptions, check_tenant


def match(value, pattern, label):
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise ValueError(f"Invalid {label}.")
    return value


def private_linux_vms(*, tenant, subscription, group, region, names, size, vnet, subnet, image_version,
                      synthetic=False):
    tenant = check_tenant(tenant)
    subscription = check_subscriptions([subscription])[0]
    group = match(group, r"[A-Za-z0-9_().-]{1,89}[A-Za-z0-9_()-]", "resource group")
    region = match(region, r"[a-z0-9]{2,32}", "region")
    size = match(size, r"Standard_[A-Za-z0-9_-]{2,40}", "VM size")
    vnet = match(vnet, r"[A-Za-z0-9][A-Za-z0-9_.-]{0,62}[A-Za-z0-9_]", "virtual network")
    subnet = match(subnet, r"[A-Za-z0-9][A-Za-z0-9_.-]{0,78}[A-Za-z0-9_]", "subnet")
    match(image_version, r"[0-9]+\.[0-9]+\.[0-9]+", "pinned image version")
    if not isinstance(names, list) or not 1 <= len(names) <= 2:
        raise ValueError("Enter one or two VM names.")
    names = [match(name, r"[a-z][a-z0-9-]{0,60}[a-z0-9]", "VM name") for name in names]
    if len(set(names)) != len(names):
        raise ValueError("VM names must be unique.")

    image = {"publisher": "Canonical", "offer": "ubuntu-24_04-lts", "sku": "server", "version": image_version}
    subnet_id = f"/subscriptions/{subscription}/resourceGroups/{group}/providers/Microsoft.Network/virtualNetworks/{vnet}/subnets/{subnet}"
    resources = []
    for name in names:
        nic = name + "-nic"
        nic_ref = f"[resourceId('Microsoft.Network/networkInterfaces', '{nic}')]"
        resources.extend([
            {"type": "Microsoft.Network/networkInterfaces", "apiVersion": "2023-09-01",
             "name": nic, "location": region, "properties": {"ipConfigurations": [
                 {"name": "private", "properties": {"privateIPAllocationMethod": "Dynamic", "subnet": {"id": subnet_id}}}
             ]}},
            {"type": "Microsoft.Compute/virtualMachines", "apiVersion": "2023-09-01",
             "name": name, "location": region, "dependsOn": [nic_ref], "properties": {
                 "hardwareProfile": {"vmSize": size},
                 "storageProfile": {"imageReference": image, "osDisk": {
                     "name": name + "-os", "createOption": "FromImage", "diskSizeGB": 32,
                     "managedDisk": {"storageAccountType": "StandardSSD_LRS"},
                 }},
                 "osProfile": {"computerName": name, "adminUsername": "cccadmin", "linuxConfiguration": {
                     "disablePasswordAuthentication": True,
                     "ssh": {"publicKeys": [{"path": "/home/cccadmin/.ssh/authorized_keys",
                                             "keyData": "[parameters('sshPublicKey')]"}]},
                 }},
                 "networkProfile": {"networkInterfaces": [{"id": nic_ref}]},
             }},
        ])
    template = {
        "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#",
        "contentVersion": "1.0.0.0", "parameters": {"sshPublicKey": {"type": "string"}}, "resources": resources,
    }
    plan = {
        "status": "draft", "synthetic": synthetic, "tenant": tenant, "subscription": subscription,
        "resource_group": group, "region": region, "vm_names": names, "size": size, "subnet_id": subnet_id,
        "inbound": "No public IP or new inbound rules. Verify effective subnet rules in Azure.",
        "monthly_estimate": None,
        "pending": [
            "Validate subnet access and effective network rules, quota, capacity, image, and policy.",
            "Check VM, NIC, and OS disk name conflicts and verify deployment permissions.",
            "Calculate compute, disk and networking costs for the target subscription.",
            "Bind the SSH public key, confirm the final plan, and verify laptop pairing.",
        ],
        "arm_template": template,
    }
    return {**plan, "plan_digest": hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()}


def sample():
    return private_linux_vms(
        tenant="00000000-0000-4000-8000-000000000001", subscription="00000000-0000-4000-8000-000000000002",
        group="rg-ccc-sample", region="uaenorth", names=["sample-vm-01", "sample-vm-02"],
        size="Standard_D2s_v5", vnet="sample-vnet", subnet="sample-private", image_version="0.0.0", synthetic=True,
    )
