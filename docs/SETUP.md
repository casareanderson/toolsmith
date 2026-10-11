# Setting up toolsmith

toolsmith is a profile for [Hermes Agent](https://github.com/NousResearch/hermes-agent), not a standalone app.
It needs a working Hermes install and Proxmox LXC containers to sandbox in, so this guide has two halves:

1. **Install and prove the safety parts**: tests, install, the guard and the area rotation. These need nothing
   but Python, and they're the blocks marked as tested. A fresh Ubuntu runner runs them every week in
   [`setup-check`](../.github/workflows/setup-check.yml).
2. **Wire it to your estate**: Discord, Proxmox, sandbox hosts, timers. CI can't prove these because they need
   your machines and accounts, so they have no test mark.

Budget about 15 minutes for the first half and an hour for the second.


![What a passing run looks like on a fresh machine (excerpt from `setup-check` on 11 October 2026). Secrets in the output are masked](setup-run.png)

*What a passing run looks like on a fresh machine (excerpt from `setup-check` on 11 October 2026). Secrets in the output are masked.*

## What you need

- A working Hermes Agent install with profiles, `pre_tool_call` shell hooks, plugins and `kanban`.
- Python 3.11 or newer with `requests` and `PyYAML` (the Hermes venv has both). The tests need only the standard
  library.
- Root SSH key access from the Hermes box to your Proxmox nodes. The gatherer reads every running CT with
  `pct exec`, read-only.
- Docker, `curl` and `systemd-run` inside the CTs you'll let it use as sandbox hosts.
- A Discord bot token and a channel for the weekly summary and the ✅ / ❌ approvals.

## 1. Get it and run the tests

Outside the Hermes venv, install the two libraries the scripts import (Debian and Ubuntu package names):

```sh setup-check
sudo apt-get update -qq && sudo apt-get install -y -qq python3-requests python3-yaml
```

```sh setup-check
git clone -q https://github.com/casareanderson/toolsmith
cd toolsmith
python3 -m unittest discover -s tests
```

You should see `Ran 13 tests` and `OK` at the end. They cover the guard and the validator, the two parts that
stop the model doing something it shouldn't or claiming something it can't back up.

## 2. Install into Hermes

`install.sh` copies the scripts, renders the profile and never overwrites your own `toolsmith.conf`,
`areas.json`, `preferences.json` or profile `config.yaml`, so it's safe to run again.

```sh setup-check
export HERMES_HOME="$HOME/.hermes"
./install.sh
```

It prints the next steps. Check the profile came out fully rendered, with no placeholders left:

```sh setup-check
! grep -l "__SCRIPTS__\|__TOOLSMITH_HOME__\|__HERMES_PY__" \
    "$HERMES_HOME/profiles/toolsmith/SOUL.md" "$HERMES_HOME/profiles/toolsmith/config.yaml"
ls "$HERMES_HOME/scripts" "$HERMES_HOME/toolsmith"
```

The SOUL has to name the real sandbox path, because that's the only path the guard lets the model run.

What lands where:

| path | what it is |
|---|---|
| `$HERMES_HOME/scripts/` | the gatherer, sandbox, guard, validator, approvals poller and the weekly runner |
| `$HERMES_HOME/toolsmith/toolsmith.conf` | your settings (from `toolsmith.conf.example`) |
| `$HERMES_HOME/toolsmith/areas.json`, `preferences.json` | the 12-area rotation and owner preferences |
| `$HERMES_HOME/toolsmith/{runs,facts,reports,quarantine,sandbox,workspace}/` | everything a run produces |
| `$HERMES_HOME/profiles/toolsmith/` | `SOUL.md`, `config.yaml`, `profile.yaml` and the `toolsmith-pii-scrub` plugin |

Set `TOOLSMITH_HOME`, `TOOLSMITH_PROFILE` or `HERMES_VENV` before `./install.sh` if yours differ from the
defaults.

## 3. Prove the guard works

The guard is a `pre_tool_call` hook that runs before every tool call on this profile. It allow-lists tools by
name, and `terminal` may only run the sandbox script, a few read-only commands inside the toolsmith tree, or a
plain HTTPS GET to a public host. It speaks JSON on stdin, so you can poke it by hand:

```sh setup-check
S="$HERMES_HOME/scripts"
echo '{"tool_name":"terminal","tool_input":{"command":"rm -rf /"}}' | python3 "$S/toolsmith_guard.py" | tee /tmp/guard-block.json
grep -q '"decision": "block"' /tmp/guard-block.json
echo '{"tool_name":"web_search","tool_input":{"query":"caddy vs traefik"}}' | python3 "$S/toolsmith_guard.py" | tee /tmp/guard-allow.json
grep -qx '{}' /tmp/guard-allow.json
```

`rm` is blocked with the reason and the list of what is allowed; a web search gets `{}`, which means allowed. In
the profile's `config.yaml` the hook is wired with `fail_closed: true`, so a crash or a timeout blocks the call too.

## 4. See the rotation and the empty approvals list

```sh setup-check
python3 "$S/toolsmith-gather.py" --list-areas
python3 "$S/toolsmith_approvals.py" list
```

The first prints each of the 12 areas with its keywords and which one is up next (`NEXT: ...`). The second prints
`{"records": []}` on a fresh install.

## 5. Wire it to your estate

Not tested in CI from here: these steps need your Discord, Proxmox nodes and sandbox CTs.

### Settings

```
$EDITOR "$HERMES_HOME/toolsmith/toolsmith.conf"
$EDITOR "$HERMES_HOME/profiles/toolsmith/config.yaml"     # OLLAMA_HOST and the model choice
```

The example file uses RFC 5737 documentation addresses (`192.0.2.x`), so every address in it needs replacing.
The keys that matter first:

| key | what to put |
|---|---|
| `TOOLSMITH_DISCORD_CHANNEL_ID` | the channel for summaries and approvals |
| `TOOLSMITH_OWNER_DISCORD_ID` | your Discord user id; the only ✅ / ❌ that counts. Empty means the poller refuses to run |
| `TOOLSMITH_PROXMOX_NODES` | `name=ip name=ip`, reached as root with key auth |
| `TOOLSMITH_SANDBOX_HOSTS` | `node_ip:ct:name:note;...` in preference order |
| `TOOLSMITH_EXCLUDED_HOSTS` | `node_ip:ct=reason;host=reason`, so a refusal is explicit |
| `TOOLSMITH_PRIVATE_DOMAINS` | your own domains; the guard never lets `curl` reach them |

The full table is in the [README](../README.md#configuration). Environment variables with the same name override
the file, and `TOOLSMITH_CONF` points at a different file.

### The one secret

The Discord bot token is read from the environment only. A line for it in `toolsmith.conf` is ignored on purpose.

```
sudo mkdir -p /etc/toolsmith
sudo install -m 600 /dev/null /etc/toolsmith/secrets.env
echo 'TOOLSMITH_DISCORD_BOT_TOKEN=your-token' | sudo tee -a /etc/toolsmith/secrets.env > /dev/null
```

The systemd units load it with `EnvironmentFile=-/etc/toolsmith/secrets.env`.

### Check the sandbox

```
S=$HERMES_HOME/scripts
$S/toolsmith_sandbox.sh hosts          # which sandbox hosts qualify, and why the others don't
$S/toolsmith_sandbox.sh refuse-test    # asks for a 2048 MB cap; it should be refused
```

Every host is re-measured before each run: rootfs at most 80% full, CT and node free memory at least twice the
cap, disk at least twice the biggest image, and no other toolsmith container running. `hosts` prints the
numbers and the reasons for each one. `refuse-test` writes a sandbox JSON and prints `SANDBOX_JSON=<path>`;
success is that it refuses.

### A dry run with nothing sent

```
$S/toolsmith-review.sh --no-post
$S/toolsmith-review.sh --area reverse-proxy --no-post    # one area
```

Success is a report and a validator JSON under `$HERMES_HOME/toolsmith/runs/`, with nothing posted to Discord.
Any recommendation whose numbers aren't in the fact lines it cites is withheld into `quarantine/` with the reason.

Then prove the approval loop with a labelled test message:

```
python3 $S/toolsmith_approvals.py post-test
python3 $S/toolsmith_approvals.py list
```

### Timers

```
sudo cp systemd/toolsmith-*.service systemd/toolsmith-*.timer /etc/systemd/system/
sudoedit /etc/systemd/system/toolsmith-review.service     # fix HOME / HERMES_HOME / paths if yours differ
sudo systemctl daemon-reload
sudo systemctl enable --now toolsmith-review.timer toolsmith-approvals.timer
```

`toolsmith-review.timer` runs on Thursdays at 10:30 and logs to `/var/log/toolsmith-review.log`.
`toolsmith-approvals.timer` checks the Discord reactions every 10 minutes.

## Common problems

| you see | what it means | what to do |
|---|---|---|
| `cp: warning: behavior of -n is non-portable and may change in future` during `install.sh` | newer coreutils warns about `cp -n`, which `install.sh` uses so it never overwrites your files | harmless; it still skips files you already have |
| `install.sh` stops after copying a few files | an older `install.sh` hit `scripts/__pycache__` left by the tests | update; the current one copies files only |
| `unreachable via pct exec (rc=255: ssh: ... Connection timed out)` in `hosts` | the Hermes box can't SSH to that node as root | check the key and the address in `TOOLSMITH_PROXMOX_NODES` / `TOOLSMITH_SANDBOX_HOSTS` |
| every host says it doesn't qualify | free memory, disk or rootfs below the thresholds | pick another CT, or free space; the reasons are printed per host |
| the approvals poller refuses to run | `TOOLSMITH_OWNER_DISCORD_ID` is empty | set it; nobody else's ✅ counts |
| a `{"decision": "block", ...}` you didn't expect in the agent log | the guard stopped a tool call | that's the guard doing its job; the reason says what is allowed |
| the report says `NOT MEASURED` for recorded incidents | `TOOLSMITH_NOTES_DIR` is empty | point it at your notes, or leave it; nothing is made up |
| a preference printed as `UNVERIFIED` | its quote can't be found in its source file any more | update `preferences.json` |
| `unknown arg ...` from `toolsmith-review.sh` | a flag it doesn't have | it takes `--no-post` and `--area NAME` |

## Update

```
cd toolsmith
git pull
HERMES_HOME=/root/.hermes ./install.sh      # replaces the scripts; keeps your conf, data and profile config
python3 -m unittest discover -s tests
```

`install.sh` doesn't overwrite an existing `SOUL.md`. To pick up a new one, move yours aside, run it again and
copy your changes back.

## Uninstall

```
sudo systemctl disable --now toolsmith-review.timer toolsmith-approvals.timer
sudo rm /etc/systemd/system/toolsmith-*.service /etc/systemd/system/toolsmith-*.timer
sudo systemctl daemon-reload
$HERMES_HOME/scripts/toolsmith_sandbox.sh sweep     # removes anything carrying a toolsmith label
rm -rf "$HERMES_HOME/profiles/toolsmith" "$HERMES_HOME/toolsmith"
rm -f "$HERMES_HOME"/scripts/toolsmith*
sudo rm -rf /etc/toolsmith
```

Run the `sweep` before deleting the scripts, so no sandbox container is left behind.
