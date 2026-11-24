"""Every Azure service in Microsoft's Retail Prices catalogue (prices.azure.com, 188 services, Nov 2026) mapped to its
Azure Pricing Calculator page (data-slug). None = the calculator has no page for it (licences, preview or retired services);
those bill lines are listed as "no calculator equivalent" instead of being dropped.
The bill's MeterCategory equals the catalogue's serviceName."""

SERVICE_SLUGS = {
    'AI Ops': 'sre-agent', 'AKS Edge Essentials': 'aks-ee', 'AKS on Azure Stack HCI': 'aks-hci', 'API Management': 'api-management',
    'Advanced Container Networking Services': 'advanced-container-networking-services', 'App Configuration': 'app-configuration',
    'Application Gateway': 'application-gateway', 'Application Insights': 'monitor', 'Automation': 'automation',
    'Azure API Center': 'api-management', 'Azure API for FHIR': 'health-data-services', 'Azure Active Directory B2C': 'active-directory-b2c',
    'Azure Active Directory for External Identities': 'microsoft-entra-external-id', 'Azure Adaptive Virtual Patching': None,
    'Azure Analysis Services': 'analysis-services', 'Azure App Service': 'app-service', 'Azure App Testing': 'load-testing',
    'Azure Arc': 'azure-arc', 'Azure Arc Enabled Databases': 'azure-arc', 'Azure Arc-enabled AKS': 'azure-arc', 'Azure Bastion': 'azure-bastion',
    'Azure Blockchain': None, 'Azure Bot Service': 'bot-services', 'Azure Chaos Studio': 'chaos-studio', 'Azure Cloud HSM': 'azure-cloud-hsm',
    'Azure Cognitive Search': 'search', 'Azure Container Apps': 'container-apps', 'Azure Cosmos DB': 'cosmos-db',
    'Azure DDOS Protection': 'ddos-protection', 'Azure DNS': 'dns', 'Azure Data Explorer': 'data-explorer', 'Azure Data Factory': 'data-factory',
    'Azure Data Factory v2': 'data-factory', 'Azure Data Share': 'data-share', 'Azure Database Migration Service': 'database-migration',
    'Azure Database for MariaDB': 'mariadb', 'Azure Database for MySQL': 'mysql', 'Azure Database for PostgreSQL': 'postgresql',
    'Azure Databricks': 'databricks', 'Azure DevOps': 'azure-devops', 'Azure Device Registry': 'iot-operations', 'Azure Firewall': 'azure-firewall',
    'Azure Firewall Manager': 'firewall-manager', 'Azure Fluid Relay': 'fluid-relay', 'Azure Front Door Service': 'frontdoor-standard-premium',
    'Azure Grafana Service': 'managed-grafana', 'Azure IoT Operations': 'iot-operations', 'Azure IoT Security': 'defender-for-cloud',
    'Azure Kubernetes Service': 'kubernetes-service', 'Azure Lab Services': 'lab-services', 'Azure Local': 'azure-arc',
    'Azure Machine Learning': 'machine-learning-service', 'Azure Mainframe Modernization Agents': None,
    'Azure Managed Instance for Apache Cassandra': 'managed-instance-apache-cassandra', 'Azure Maps': 'azure-maps', 'Azure Monitor': 'monitor',
    'Azure NetApp Files': 'netapp', 'Azure Operator Nexus': None, 'Azure Orbital': None, 'Azure Orbital Edge': None, 'Azure Policy': 'azure-policy',
    'Azure Purview': 'purview', 'Azure Remote Rendering': 'remote-rendering', 'Azure Resource Mover': 'resource-mover',
    'Azure Route Server': 'route-server', 'Azure SQL Edge': 'azure-sql-edge', 'Azure Site Recovery': 'site-recovery',
    'Azure Spring Cloud': 'spring-apps', 'Azure Stack Edge': 'azure-stack-edge', 'Azure Stack HCI': 'azure-arc',
    'Azure Synapse Analytics': 'synapse-analytics', 'Azure Update Manager': 'update-management-center', 'Azure VM Image Builder': None,
    'Azure Virtual Enclaves': 'azure-virtual-enclaves', 'Azure confidential ledger': 'confidential-ledger', 'Backup': 'backup',
    'Bandwidth': 'bandwidth', 'BizTalk Services': None, 'Change Tracking and Inventory': 'automation', 'Cloud Services': 'cloud-services',
    'Community Training': None, 'Container Instances': 'container-instances', 'Container Registry': 'container-registry',
    'Content Delivery Network': 'cdn', 'Data Box': 'databox', 'Data Catalog': 'data-catalog', 'Data Lake Store': 'storage', 'Dataverse': None,
    'Digital Twins': 'digital-twins', 'Durable Task Scheduler': 'functions', 'Dynamics 365 for Customer Insights': None,
    'Email': 'communication-services', 'Energy Data Manager': 'energy-data-services', 'Event Grid': 'event-grid', 'Event Hubs': 'event-hubs',
    'ExpressRoute': 'expressroute', 'Firmware Analysis': 'defender-for-cloud', 'Foundry Models': 'openai-service', 'Foundry Tools': 'cognitive-services',
    'Functions': 'functions', 'GitHub': 'githubenterprise', 'GitHub AE': 'githubenterprise', 'HDInsight': 'hdinsight', 'HPC Cache': None,
    'High Volume Email': 'communication-services', 'Insight and Analytics': 'monitor', 'Intelligent Recommendations': None,
    'IoT Central': 'iot-central', 'IoT Hub': 'iot-hub', 'Key Vault': 'key-vault', 'Load Balancer': 'load-balancer', 'Log Analytics': 'monitor',
    'Logic Apps': 'logic-apps', 'MS Bing Services': None, 'Machine Learning Studio': 'machine-learning-studio', 'Messaging': 'communication-services',
    'Microsoft Azure Payment HSM': 'azure-dedicated-hsm', 'Microsoft Azure Peering Service': None, 'Microsoft Copilot Studio': None,
    'Microsoft Defender': 'defender-for-cloud', 'Microsoft Defender Experts': 'microsoft-defender-isoc', 'Microsoft Defender for Cloud': 'defender-for-cloud',
    'Microsoft Discovery': None, 'Microsoft Dragon Copilot': None, 'Microsoft Entra': 'active-directory', 'Microsoft Entra Domain Services': 'active-directory',
    'Microsoft Entra Verified ID': 'active-directory', 'Microsoft Fabric': 'microsoft-fabric', 'Microsoft Genomics': 'genomics',
    'Microsoft Graph Services': None, 'Microsoft Graph data connect': 'graph-data-connect', 'Microsoft Planetary Computer Pro': 'planetary-computer-pro',
    'Microsoft Playwright Testing': 'load-testing', 'Microsoft Purview': 'purview', 'Microsoft Security Copilot': 'microsoft-security-copilot',
    'Multi-Factor Authentication': 'active-directory', 'NAT Gateway': 'virtual-network-nat', 'Network Traversal': 'communication-services',
    'Network Watcher': 'network-watcher', 'Notification Hubs': 'notification-hubs', 'Packet Core': None, 'Perception': None,
    'Phone Numbers': 'communication-services', 'PlayFab': None, 'Power Apps': None, 'Power Automate': None, 'Power BI': None,
    'Power BI Embedded': 'power-bi-embedded', 'Power Pages': None, 'Private Mobile Network': None, 'Quantum Computing': None,
    'Redis Cache': 'redis-cache', 'Resiliency': None, 'Routing': 'communication-services', 'SMS': 'communication-services', 'SQL DB Edge': 'azure-sql-edge',
    'SQL Data Warehouse': 'synapse-analytics', 'SQL Database': 'azure-sql-database', 'SQL Managed Instance': 'azure-sql',
    'SQL Server Stretch Database': None, 'Scheduler': 'scheduler', 'Sentinel': 'azure-sentinel', 'Service Bus': 'service-bus',
    'Service Fabric': 'service-fabric', 'Service Fabric Mesh': 'service-fabric', 'SignalR': 'signalr-service', 'Specialized Compute': 'virtual-machines',
    'StorSimple': None, 'Storage': 'storage', 'Stream Analytics': 'stream-analytics', 'Syntex': None, 'Test Base': None,
    'Time Series Insights': 'time-series-insights', 'Traffic Manager': 'traffic-manager', 'Trusted Signing': 'artifact-signing',
    'VPN Gateway': 'vpn-gateway', 'Virtual Machines': 'virtual-machines', 'Virtual Machines Licenses': 'virtual-machines',
    'Virtual Network': 'virtual-network', 'Virtual WAN': 'virtual-wan', 'Visual Studio Codespaces': None, 'Visual Studio Subscription': None,
    'Voice': 'communication-services', 'Voice Core': 'communication-services', 'Web PubSub': 'web-pubsub',
    'Windows 10 IoT Core Services': 'windows-10-iot-core', 'Windows 365 Agents': None, 'Windows Virtual Desktop': 'virtual-desktop',
    # names that appear on bills but not (or under another name) in the retail catalogue
    'Azure Cognitive Services': 'cognitive-services', 'Cognitive Services': 'cognitive-services', 'Azure OpenAI Service': 'openai-service',
    'Azure AI Search': 'search', 'Azure Container Registry': 'container-registry', 'Container Apps': 'container-apps',
    'Azure Database for PostgreSQL Flexible Server': 'postgresql', 'Azure Database for MySQL Flexible Server': 'mysql',
    'Microsoft Sentinel': 'azure-sentinel', 'Azure Front Door': 'frontdoor-standard-premium', 'Azure Communication Services': 'communication-services',
    'Advanced Threat Protection': 'defender-for-cloud', 'Security Center': 'defender-for-cloud', 'Azure Defender': 'defender-for-cloud',
    'Azure Data Lake Storage': 'storage', 'Azure Managed Redis': 'managed-redis', 'Azure Kubernetes Service Edge Essentials': 'aks-ee',
}

SERVICE_NAMES = {v: k for k, v in reversed(list(SERVICE_SLUGS.items())) if v}

# Extra settings for pages that charge a fixed fee even at zero quantity, so a "set quantity" placeholder costs 0.
ZERO_OVERRIDES = {'ddos-protection': {'tier': 'ipprotection'}, 'frontdoor-standard-premium': {'enableBaseCost': False},
                  'expressroute': {'circuits': 0, 'globalReach': False},
                  'container-storage': {'managedDisks': 0, 'orchestrationCosts': 0, 'storageV2Number': 0, 'snapshotUnits': 0},
                  'documentdb': {'documentDBVcoreNode': 0, 'documentDBVcoreHour': 0}}

# Calculator pages whose charges appear on the bill under a broader service name (Storage, Virtual Machines, ...):
# the resource type decides the page. Checked against the calculator's 153 pages (Nov 2026).
RTYPE_SLUGS = {
    'microsoft.elasticsan/elasticsans': 'azure-elastic-san', 'microsoft.avs/privateclouds': 'azure-vmware',
    'microsoft.batch/batchaccounts': 'batch', 'microsoft.voiceservices/communicationsgateways': 'communications-gateway',
    'microsoft.containerstorage/pools': 'container-storage', 'microsoft.easm/workspaces': 'defender-easm',
    'microsoft.documentdb/mongoclusters': 'documentdb', 'microsoft.hybridcompute/licenses': 'extended-security-updates',
    'microsoft.healthbot/healthbots': 'healthcare-agent-service', 'microsoft.solutions/applications': 'managed-applications',
    'microsoft.storagecache/amlfilesystems': 'managed-lustre', 'microsoft.media/mediaservices': 'media-services',
    'microsoft.redhatopenshift/openshiftclusters': 'openshift', 'microsoft.storageactions/storagetasks': 'storage-actions',
    'microsoft.videoindexer/accounts': 'video-indexer', 'microsoft.compute/hostgroups': 'virtual-machines\\dedicated-host',
    'microsoft.network/networkmanagers': 'virtual-network-manager', 'microsoft.compute/virtualmachinescalesets': 'virtual-machine-scale-sets',
    'microsoft.web/staticsites': 'app-service\\static', 'microsoft.network/publicipaddresses': 'ip-addresses',
    'microsoft.network/publicipprefixes': 'ip-addresses', 'microsoft.baremetalinfrastructure/baremetalinstances': 'nutanix-on-azure',
    'microsoft.horizondb/clusters': 'horizondb', 'microsoft.edge/sites': 'iot-edge', 'microsoft.storagediscovery/workspaces': 'azure-storage-discovery',
    'microsoft.migrate/assessmentprojects': 'azure-migrate', 'microsoft.migrate/migrateprojects': 'azure-migrate',
    'microsoft.cognitiveservices/accounts#formrecognizer': 'form-recognizer', 'microsoft.cognitiveservices/accounts#aiservices': 'cognitive-services',
    'microsoft.cognitiveservices/accounts#openai': 'openai-service', 'microsoft.cognitiveservices/accounts#contentunderstanding': 'content-understanding',
}
# Free services (no charge of their own): Advisor, Lighthouse, Cost Management, Open Datasets.
FREE_SLUGS = {'advisor', 'azure-lighthouse', 'cost-management', 'open-datasets'}
