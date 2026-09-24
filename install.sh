#!/bin/bash
# install.sh — copy toolsmith into a Hermes install. Idempotent; never overwrites
# your toolsmith.conf, areas.json, preferences.json or profile config.yaml.
#   HERMES_HOME=/root/.hermes ./install.sh
set -euo pipefail
HERMES_HOME=${HERMES_HOME:-$HOME/.hermes}
TS=${TOOLSMITH_HOME:-$HERMES_HOME/toolsmith}
S=$HERMES_HOME/scripts
P=$HERMES_HOME/profiles/${TOOLSMITH_PROFILE:-toolsmith}
PY=${HERMES_VENV:-/usr/local/lib/hermes-agent/venv}/bin/python
SRC=$(cd "$(dirname "$0")" && pwd)

mkdir -p "$S" "$TS"/{workspace,sandbox,reports,quarantine,facts,runs} "$P/plugins"
install -m 0755 "$SRC"/scripts/* "$S"/
cp -n "$SRC/toolsmith.conf.example" "$TS/toolsmith.conf"
cp -n "$SRC/data/areas.json" "$TS/areas.json"
cp -n "$SRC/data/preferences.json" "$TS/preferences.json"
render() { sed -e "s#__SCRIPTS__#$S#g" -e "s#__TOOLSMITH_HOME__#$TS#g" -e "s#__HERMES_PY__#$PY#g" "$1"; }
[ -e "$P/SOUL.md" ] || render "$SRC/profile/SOUL.md" > "$P/SOUL.md"
[ -e "$P/config.yaml" ] || render "$SRC/profile/config.yaml.example" > "$P/config.yaml"
[ -e "$P/profile.yaml" ] || cp "$SRC/profile/profile.yaml" "$P/profile.yaml"
cp -r "$SRC/profile/plugins/toolsmith-pii-scrub" "$P/plugins/"
echo "installed. Next:"
echo "  1. edit $TS/toolsmith.conf (and $P/config.yaml: OLLAMA_HOST, models)"
echo "  2. put TOOLSMITH_DISCORD_BOT_TOKEN in /etc/toolsmith/secrets.env (chmod 600)"
echo "  3. $S/toolsmith_sandbox.sh hosts         # which sandbox hosts qualify"
echo "  4. $S/toolsmith_sandbox.sh refuse-test   # prove the refusal path"
echo "  5. $S/toolsmith-review.sh --no-post      # dry run, nothing sent"
echo "  6. copy systemd/*.service|*.timer to /etc/systemd/system (edit paths), enable the timers"
