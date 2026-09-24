# Withheld by toolsmith_validate.py — run 20260914-2305

## Summary withheld
- number(s) 15, 2.0 next to [F021], [F046], [F054], [F055], [F100], [F099] are not in those fact lines

## REC 0: Keep the incumbent — Langfuse v4 on CT112
Reasons:
- no candidate source URL
- number(s) 15 next to [F011], [F012], [F013], [F014], [F015], [F016], [F021], [F046], [F054], [F055] are not in those fact lines

```
- Incumbent: Langfuse v4 (self-hosted) — why: on 4-week trial, keep-vs-retire decision due ~2026-10-12 [F007]; healthy now — all six containers running with restart counts 0 (web restart 4) [F011][F012][F013][F014][F015][F016]. Pains: 6-container stack uses 1961.1 MB [F021] for 219 events / 7 days [F046] (~15 MB disk [F054]-[F055]); covers ~7% of agent traffic [F100]; OpenTelemetry conflict in venv (langfuse==4.14.4 [F090] vs pinned opentelemetry-sdk==1.39.1 [F094]) [F099].
- Candidate: keep what we run.
- Licence: MIT Expat outside ee/, web/src/ee/, worker/src/ee/ dirs; those under proprietary licence — class: OSI core with source-available ee/ — source: https://github.com/langfuse/langfuse/blob/main/LICENSE
- Health: last release 2026-09-14 (v4.36.0) — source: https://github.com/langfuse/langfuse/releases
- Resources: needs ≥2 CPU + 4 GB RAM for all app containers (per docs) — source: https://langfuse.com/self-hosting/deployment/infrastructure/containers ; estate actually uses 1961.1 MB across 6 containers [F021], web 669.2 MB [F012], worker 591.2 MB [F013], clickhouse 518.5 MB [F011]; target CT112 has 4440 MB available [F037]
- Sandbox: not tested (live incumbent; no migration involved)
- Migration effort: none (this IS the incumbent)
- Rollback: n/a
- Verdict: ADOPT-TRIAL — run the existing 4-week trial [F007] to its ~2026-10-12 decision date; it is healthy and the replacement candidates do not yet beat it.
```

## REC 1: OpenLIT
Reasons:
- number(s) 3 next to [F021], [F046] are not in those fact lines

```
- Incumbent: Langfuse v4 — why: same LLM/agent-trace role, lighter footprint and cleaner licence; incumbent pain is 6 containers at 1961.1 MB [F021] for 219 events/wk [F046]. OpenLIT needs only 3 components and can reuse an existing ClickHouse + OTel Collector — source: https://docs.openlit.io/latest/openlit/installation
- Candidate: OpenLIT, open-source observability & evaluation for AI agents, Apache-2.0 — source: https://github.com/openlit/openlit
- Licence: Apache-2.0 — class: OSI — source: https://raw.githubusercontent.com/openlit/openlit/main/LICENSE
- Health: last release 2026-09-10 (openlit 2.1.0) — source: https://github.com/openlit/openlit/releases
- Resources: lightweight 3-component stack (OpenLIT + ClickHouse + OTel Collector); can reuse existing ClickHouse/OTel — source: https://docs.openlit.io/latest/openlit/installation ; single image ghcr.io/openlit/openlit:latest in official compose — source: https://raw.githubusercontent.com/openlit/openlit/main/docker-compose.yml ; target CT112 free 4440 MB RAM [F037], rootfs 14% used / 49278 MB free [F037]
- Sandbox: ts-20260914-230927-e882 — status failed; pull_s 2.0, log_tail [], pull failed rc=1 "repository does not exist" (image openlit/backend:openlit-2.1.0 — tag does not exist on Docker Hub). NOTE: the correct image is ghcr.io/openlit/openlit:latest [compose file]; sandbox spec used a wrong tag, so OpenLIT itself is unvalidated, not disproven.
- Migration effort: L — re-instrument Hermes (the plugin writes via HERMES_LANGFUSE_* env, enabled on all profiles [F078]-[F089]); data not portable between ClickHouse schemas; agent-traces canary/weekly consumers read Langfuse ClickHouse [F096][F097]
- Rollback: keep the six langfuse containers; disable OpenLIT plugin envs, revert to HERMES_LANGFUSE_* — traces still recorded by incumbent throughout
- Verdict: WATCH — fits (clean Apache-2.0, 3-component, reuses existing ClickHouse) but the sandbox used a non-existent image tag, so it was not validated; re-test with ghcr.io/openlit/openlit:latest before any trial.
```

