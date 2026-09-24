#!/bin/bash
# toolsmith_sandbox.sh — the ONLY way the toolsmith profile runs a candidate tool.
# Thin wrapper so the guard hook can allow exactly one path; logic lives in
# toolsmith_sandbox.py next to it. Usage: hosts | run --spec FILE | sweep | refuse-test
exec /usr/bin/python3 "$(dirname "$(readlink -f "$0")")/toolsmith_sandbox.py" "$@"
