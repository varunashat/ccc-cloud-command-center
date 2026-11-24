CCC Edge/Chrome extension v2.0.1

What's new: the Environment & Deployment page in Cloud Command Center can now drive YOUR signed-in Azure portal tab
from a chat. Azure AI Foundry (the CCC app's own resource) decides each step; this extension clicks/types in the tab.

Install / update
1. Edge: edge://extensions  (Chrome: chrome://extensions) -> turn on Developer mode.
2. Remove the old CCC extension, then "Load unpacked" and pick this folder.
3. Reload the Cloud Command Center tab.

Safety built in
- After updating files, click Reload on the extension in chrome://extensions.
- Acts only on one tab, and only on portal.azure.com / Microsoft sign-in hosts (https).
- Never types into password fields or fields labelled password/secret/key/token/card; it asks you instead.
- You enter passwords and MFA yourself. Press Stop on the page at any time. 250-step cap per task.
