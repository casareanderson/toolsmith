"""toolsmith_config.py — one place for every path and estate-specific setting.

Everything site-specific lives in toolsmith.conf (KEY=VALUE, '#' comments), found at
$TOOLSMITH_CONF, else $TOOLSMITH_HOME/toolsmith.conf. Environment variables override
the file. Secrets are NEVER read from the file: only from the environment.

See toolsmith.conf.example for every key.
"""
import os

HERMES_HOME = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
TS_ROOT = os.environ.get("TOOLSMITH_HOME") or os.path.join(HERMES_HOME, "toolsmith")
SCRIPTS = os.path.dirname(os.path.realpath(__file__))
CONF = os.environ.get("TOOLSMITH_CONF") or os.path.join(TS_ROOT, "toolsmith.conf")

# Keys that must never come from a file on disk.
SECRET_KEYS = {"TOOLSMITH_DISCORD_BOT_TOKEN"}


def conf():
    c = {}
    try:
        for line in open(CONF):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                k = k.strip()
                if k in SECRET_KEYS:
                    continue
                c[k] = v.strip().strip("'\"")
    except OSError:
        pass
    for k, v in os.environ.items():
        if k.startswith(("TOOLSMITH_", "HERMES_")):
            c[k] = v
    return c


def get(key, default=""):
    return conf().get(key) or default


def pairs(key):
    """'a=b c=d' -> [('a','b'), ('c','d')]"""
    out = []
    for item in get(key).split():
        k, _, v = item.partition("=")
        if k and v:
            out.append((k, v))
    return out


def sandbox_hosts():
    """TOOLSMITH_SANDBOX_HOSTS='node:ct:name:note with spaces;node:ct:name:note' in preference order."""
    out = []
    for item in get("TOOLSMITH_SANDBOX_HOSTS").split(";"):
        p = [x.strip() for x in item.split(":", 3)]
        if len(p) >= 3 and p[0] and p[1].isdigit():
            out.append((p[0], int(p[1]), p[2], p[3] if len(p) > 3 else ""))
    return out


def excluded_hosts():
    """TOOLSMITH_EXCLUDED_HOSTS='node:ct=reason;host=reason' — recorded so a refusal is explicit."""
    out = {}
    for item in get("TOOLSMITH_EXCLUDED_HOSTS").split(";"):
        k, _, v = item.partition("=")
        if k.strip():
            out[k.strip()] = v.strip() or "excluded"
    return out


HERMES_BIN = get("HERMES_BIN", "/usr/local/bin/hermes")
HERMES_VENV = get("HERMES_VENV", "/usr/local/lib/hermes-agent/venv")
HERMES_SRC = get("HERMES_SRC", "/usr/local/lib/hermes-agent")
PROFILE = get("TOOLSMITH_PROFILE", "toolsmith")
PROFILE_DIR = os.path.join(HERMES_HOME, "profiles", PROFILE)
