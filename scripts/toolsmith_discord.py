#!/usr/bin/env python3
"""toolsmith_discord.py — Discord REST helper for toolsmith (post-with-id, reactions, replies).

Approvals need the message id back, so this talks to the Discord REST API
directly instead of going through a fire-and-forget notifier.

Secret (environment ONLY, never the conf file, never printed or logged):
  TOOLSMITH_DISCORD_BOT_TOKEN    bot token; the bot needs Send Messages, Add
                                 Reactions and Read Message History in the channel

Config (non-secret) from toolsmith.conf:
  TOOLSMITH_DISCORD_CHANNEL_ID   channel for every toolsmith report/notice
  TOOLSMITH_OWNER_DISCORD_ID     the only user whose reactions count
"""
import os
import sys
import time
import urllib.parse

import requests

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import toolsmith_config as cfg  # noqa: E402

API = "https://discord.com/api/v10"
APPROVE, REJECT = "\u2705", "\u274c"   # ✅ ❌


def conf():
    return cfg.conf()


def channel_id():
    ch = conf().get("TOOLSMITH_DISCORD_CHANNEL_ID")
    if not ch:
        raise RuntimeError("TOOLSMITH_DISCORD_CHANNEL_ID is not set")
    return ch


def _token():
    t = os.environ.get("TOOLSMITH_DISCORD_BOT_TOKEN")
    if not t:
        raise RuntimeError("TOOLSMITH_DISCORD_BOT_TOKEN is not set in the environment")
    return t


def _headers():
    return {"Authorization": f"Bot {_token()}", "Content-Type": "application/json"}


def _req(method, path, **kw):
    last = None
    for attempt in range(5):
        try:
            r = requests.request(method, API + path, headers=_headers(), timeout=15, **kw)
        except requests.RequestException as e:
            last = f"{type(e).__name__}"
            time.sleep(2 + 3 * attempt)
            continue
        if r.status_code == 429:
            try:
                wait = float(r.json().get("retry_after", 2))
            except Exception:
                wait = 2
            time.sleep(min(wait, 30) + 0.5)
            continue
        if r.status_code >= 500:
            last = f"HTTP {r.status_code}"
            time.sleep(2 + 3 * attempt)
            continue
        return r
    raise RuntimeError(f"discord {method} {path.split('?')[0]} failed: {last}")


def bot_user_id():
    r = _req("GET", "/users/@me")
    r.raise_for_status()
    return r.json()["id"]


def post(content, channel=None, reply_to=None):
    """Post one message (<=2000 chars). Returns the message id."""
    channel = channel or channel_id()
    body = {"content": content[:1990], "allowed_mentions": {"parse": []}}
    if reply_to:
        body["message_reference"] = {"message_id": str(reply_to), "fail_if_not_exists": False}
    r = _req("POST", f"/channels/{channel}/messages", json=body)
    r.raise_for_status()
    return r.json()["id"]


def post_long(content, channel=None):
    """Chunk a long message on line boundaries. Returns the list of message ids."""
    ids, chunk = [], ""
    for line in content.splitlines(keepends=True):
        if len(chunk) + len(line) > 1900:
            ids.append(post(chunk, channel)); chunk = ""
        chunk += line[:1900]
    if chunk.strip():
        ids.append(post(chunk, channel))
    return ids


def react(message_id, emoji, channel=None):
    channel = channel or channel_id()
    e = urllib.parse.quote(emoji)
    r = _req("PUT", f"/channels/{channel}/messages/{message_id}/reactions/{e}/@me")
    r.raise_for_status()


def reactors(message_id, emoji, channel=None):
    """User ids that reacted with emoji. Returns None if the message is gone."""
    channel = channel or channel_id()
    e = urllib.parse.quote(emoji)
    users, after = [], None
    while True:
        q = "?limit=100" + (f"&after={after}" if after else "")
        r = _req("GET", f"/channels/{channel}/messages/{message_id}/reactions/{e}{q}")
        if r.status_code == 404:
            code = (r.json() or {}).get("code") if r.headers.get("content-type", "").startswith("application/json") else None
            return None if code == 10008 else users   # 10008 Unknown Message; 10014 Unknown Emoji = nobody reacted
        r.raise_for_status()
        page = r.json()
        users += [u["id"] for u in page]
        if len(page) < 100:
            return users
        after = page[-1]["id"]


if __name__ == "__main__":
    # `toolsmith_discord.py -` posts stdin to the toolsmith channel (chunked).
    if len(sys.argv) > 1 and sys.argv[1] == "-":
        ok = post_long(sys.stdin.read())
        sys.exit(0 if ok else 1)
    print("usage: toolsmith_discord.py -   (posts stdin to TOOLSMITH_DISCORD_CHANNEL_ID)", file=sys.stderr)
    sys.exit(2)
