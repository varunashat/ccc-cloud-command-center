"""Safety policy shared by the laptop executor (enforces) and the CCC server (vets before sending).

classify(tool, args) -> (kind, reason)
  forbidden : never runs (token / key / cookie / credential extraction, shells that hide intent)
  approval  : destructive or scope-changing; runs only with the operator's explicit approval of THAT command
  write     : creates or changes resources; runs only after the operator approved the plan
  readonly  : safe to run
Everything here is a guard, not a promise that a command is harmless; the operator approves the plan.
"""
import re
from pathlib import Path

TOOLS = ('az', 'git', 'gh', 'pwsh')
MAX_ARGS = 60
MAX_ARG_LEN = 4000

FORBIDDEN = [
    (r'get-access-token|get-azaccesstoken|account\s+get-access-token', 'token extraction'),
    (r'auth\s+token|--show-token|auth\s+status\s+.*(-t\b|--show)', 'token extraction'),
    (r'list-?keys|listkeys|regenerate-?key|list-?credentials|keys\s+list|show-connection-string|--query\s+\S*(key|secret|password)', 'key extraction'),
    (r'keyvault\s+secret\s+(show|download|list)|secret\s+show', 'secret extraction'),
    (r'credential(\s|$)|git-credential|credential\.helper', 'credential access'),
    (r'\.azure[\\/]|msal_token_cache|accesstokens\.json|azureprofile|\.ssh|id_rsa|login data|cookies|\\appdata\\', 'credential or cookie files'),
    (r'invoke-expression|\biex\b|-encodedcommand|-enc\s|downloadstring|invoke-webrequest|invoke-restmethod|start-process', 'indirect or network execution'),
    (r'get-credential|convertfrom-securestring|securestring', 'credential access'),
    (r'password|passwd|--client-secret|--secret|--sas-token', 'secret on the command line'),
]
APPROVAL = [
    (r'\b(delete|remove|purge|destroy|deallocate|erase|revoke|disable)\b', 'destructive'),
    (r'\b(stop|restart|reset|swap|move|failover)\b', 'disruptive'),
    (r'role\s+assignment\s+(create)|role\s+definition|policy\s+assignment|lock\s+(create)', 'changes permissions or governance'),
    (r'account\s+set|--subscription\b.*\bset\b|config\s+set', 'changes scope'),
    (r'\bpush\b|--force|-f\b.*\bpush|reset\s+--hard|clean\s+-[a-z]*f|branch\s+-D|rebase|checkout\s+--\s|restore\b', 'git history or working-tree change'),
    (r'gh\s+(repo|release|pr|issue)\s+(delete|create|merge|close)|gh\s+api\b.*(-X|--method)\s*(POST|PUT|PATCH|DELETE)', 'GitHub write'),
]
WRITE_AZ = re.compile(r'\b(create|update|add|set|deploy|up|start|assign|import|upload|apply|enable|new|scale|attach|connect|link)\b', re.I)
WRITE_GIT = re.compile(r'\b(add|commit|checkout|switch|merge|pull|stash|tag|init|clone|fetch)\b', re.I)
READ_PWSH = re.compile(r'^\s*(get-[a-z]+|select-object|where-object|format-[a-z]+|write-output|test-path|resolve-path|\$psversiontable)', re.I)
SENSITIVE_FILE = re.compile(r'(^|[\\/])(\.env(\..*)?|.*\.(pem|pfx|key|p12|kdbx|ppk)|id_rsa.*|credentials?(\.json)?|.*secrets?.*\.(json|ya?ml|txt)|.*token.*|.*password.*)$', re.I)


def classify(tool, args):
    if tool not in TOOLS:
        return 'forbidden', f'tool "{tool}" is not allowed (az, git, gh, pwsh only)'
    if not isinstance(args, list) or not args or len(args) > MAX_ARGS or any(not isinstance(a, str) or len(a) > MAX_ARG_LEN for a in args):
        return 'forbidden', 'arguments must be a short list of strings'
    text = ' '.join(args)
    low = text.lower()
    for pattern, why in FORBIDDEN:
        if re.search(pattern, low if 'iex' in pattern or 'invoke' in pattern else low):
            return 'forbidden', why
    for pattern, why in APPROVAL:
        if re.search(pattern, text, re.I):
            return 'approval', why
    if tool == 'pwsh':
        return ('readonly', '') if all(READ_PWSH.match(part) for part in re.split(r'\s*[;|]\s*', text) if part.strip()) else ('approval', 'PowerShell that is not plainly read-only')
    if tool == 'az':
        if args[0] in ('login', 'logout'):
            return 'approval', 'sign-in is done by you interactively'
        verbs = [a for a in args[1:6] if not a.startswith('-')]
        return ('write', 'creates or changes Azure resources') if any(WRITE_AZ.fullmatch(v) for v in verbs) else ('readonly', '')
    if tool == 'git':
        return ('write', 'changes the local repo') if WRITE_GIT.search(args[0]) else ('readonly', '')
    if tool == 'gh':
        return ('approval', 'GitHub sign-in is done by you interactively') if args[:2] == ['auth', 'login'] else ('readonly', '')
    return 'forbidden', 'unrecognised'


SECRETS = [
    (re.compile(r'eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}'), '[token removed]'),
    (re.compile(r'(?i)(AccountKey|SharedAccessKey|Password|Pwd|client_secret|secret|token|sig)=([^;&\s"\']+)'), r'\1=[removed]'),
    (re.compile(r'(?i)("(?:password|secret|key|token|adminPassword|connectionString|primaryKey|secondaryKey|value)"\s*:\s*")[^"]{8,}(")'), r'\1[removed]\2'),
    (re.compile(r'gh[pousr]_[A-Za-z0-9]{20,}'), '[github token removed]'),
]


def sanitize(text, limit=12000):
    text = str(text or '')
    for pattern, repl in SECRETS:
        text = pattern.sub(repl, text)
    return text if len(text) <= limit else text[:limit] + f'\n...[{len(text) - limit} more characters not shown]'


def path_inside(path, folders):
    """True when `path` resolves inside one of the operator-selected folders and is not a credential-like file."""
    try:
        target = Path(path).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return False
    if SENSITIVE_FILE.search(str(target)):
        return False
    for folder in folders:
        root = Path(folder).expanduser().resolve()
        if target == root or root in target.parents:
            return True
    return False
