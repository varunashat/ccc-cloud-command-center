# CCC laptop executor

Lets the **Environment & Deployment > Run on my laptop** chat run Azure CLI, Git, GitHub CLI and PowerShell on *your* Windows laptop,
and read folders you choose. Azure AI Foundry (your resource) does all the reasoning; nothing else is called.

Install: run `Install-CCC-Executor.ps1` (no admin), pick folders, then use the desktop shortcut **Start CCC Executor**.
Connect: type the pairing code from the executor window into the page and press **Connect**. **Disconnect** (or closing the window) ends access.

What it will not do: print/extract passwords, tokens, cookies, keys or connection strings (commands that do so are refused and outputs are scrubbed);
touch files outside your folders or credential-looking files (.env, *.pem, ...); run tools other than az, git, gh, pwsh.
Approvals: read-only commands run freely; commands that create/change resources run only after you approve the plan (with a priced estimate);
delete / stop / reset / role / push / force / non-read-only PowerShell need your approval of that exact command.
You sign in yourself (`az login`, `gh auth login`), MFA included.

Tests: `python -m unittest tests.test_laptop_policy tests.test_laptop_executor tests.test_laptop_agent` and `python tests/e2e/run_laptop_e2e.py`.
Status: tested on Linux with git and gh; the Windows installer, `az` and PowerShell paths have not been run yet.
