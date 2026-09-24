# Toolsmith report — observability — run 20260914-2305

_Validated by toolsmith_validate.py: estate facts checked against the fact pack, URLs against this run's tool calls, sandbox claims against sandbox JSON._

**Summary withheld by the validator** (number(s) 15, 2.0 next to [F021], [F046], [F054], [F055], [F100], [F099] are not in those fact lines). Deterministic summary of the recommendations that passed:
- REJECT: SigNoz
- REJECT: HyperDX
- REJECT: Grafana OSS (read-only complement)

### REC 2: SigNoz
- Incumbent: Langfuse v4 — why: incumbent pain is footprint 1961.1 MB / 6 containers [F021] for 219 events/wk [F046]; SigNoz is heavier, not lighter, and brings its own ClickHouse into conflict with the estate's ClickHouse on CT112 [F011].
- Candidate: SigNoz, OpenTelemetry-native full-stack observability (traces, logs, metrics, alerts) — source: https://github.com/SigNoz/signoz
- Licence: MIT Expat outside ee/ and cmd/enterprise/; those under proprietary licence — class: OSI core with source-available ee/ — source: https://github.com/SigNoz/signoz/blob/main/LICENSE
- Health: last release 2026-09-13 (v0.141.1) — source: https://github.com/SigNoz/signoz/releases
- Resources: needs 4 GB min RAM for a docker install (multi-container: ClickHouse, Postgres, keeper, collector, frontend); own ClickHouse conflicts with CT112's existing ClickHouse [F011]. Re-fetch of https://docs.signoz.io/docs/install/docker/ was blocked this run; figure carried from phase-1 notes and flagged unconfirmed.
- Sandbox: not tested (needs ≥4 GB RAM and multi-container orchestration; exceeds sandbox caps)
- Migration effort: L — full re-deploy, own ClickHouse on top of estate's, new frontend; ~7% coverage problem [F100] unaffected
- Rollback: keep incumbent containers; nothing changes until SigNoz is retired
- Verdict: REJECT — heavier than the incumbent, duplicates ClickHouse, and offers nothing for the measured ~7% coverage pain [F100].

### REC 3: HyperDX
- Incumbent: Langfuse v4 — why: incumbent is LLM/agent-trace scoped; HyperDX is full-stack SRE observability (logs, session replay, APM), which doesn't address the measured Langfuse pain (~7% agent coverage [F100], 6 containers at 1961.1 MB [F021]).
- Candidate: HyperDX (now a ClickStack component), logs+traces+session-replay observability on ClickHouse — source: https://github.com/hyperdxio/hyperdx
- Licence: MIT License — class: OSI — source: https://github.com/hyperdxio/hyperdx/blob/main/LICENSE
- Health: repo actively committed 2026-09-14 [README "latest commit Sep 14, 2026"] — source: https://github.com/hyperdxio/hyperdx ; CISA bulletins reference HyperDX version ≥2.31.0 (sb26-208) and a role-enforcement issue (sb26-243) indicating active versioning — source: https://www.cisa.gov/news-events/bulletins/sb26-208 and https://www.cisa.gov/news-events/bulletins/sb26-243. CORRECTION to phase-1: NOT abandoned; actively maintained under ClickStack.
- Resources: needs ≥4 GB RAM + 2 cores per project README — source: https://github.com/hyperdxio/hyperdx ; target CT112 free 4440 MB [F037] would be consumed by this single tool before any agent tracing
- Sandbox: not tested (needs ≥4 GB RAM + 2 cores; exceeds caps)
- Migration effort: L — full-stack SRE tool, no LLM-prompt/token/cost surface; would not replace Langfuse's role
- Rollback: keep incumbent unaffected
- Verdict: REJECT for this role — heavy (≥4 GB + 2 cores) and aimed at infra observability, not the LLM-agent tracing that is Langfuse's job here.

### REC 4: Grafana OSS (read-only complement)
- Incumbent: none directly — Grafana is a dashboards complement, NOT a Langfuse replacement: it would read the same ClickHouse Langfuse already fills [F011] without changing ingestion.
- Candidate: GNU [PERSON_NAME] OSS, open visualization/alerting over existing data sources — source: https://github.com/grafana/grafana
- Licence: GNU AGPL-3.0 — class: OSI (copyleft) — source: https://github.com/grafana/grafana/blob/main/LICENSE
- Health: last release 2026-09-01 (13.2.1) — source: https://grafana.com/grafana/download
- Resources: single container, SQLite-capable, low footprint (no hard min stated on download page) — source: https://grafana.com/grafana/download ; target CT112 free 4440 MB [F037]
- Sandbox: not tested (no Grafana sandbox run in this cycle's results)
- Migration effort: S — deploy one container pointed at Langfuse's ClickHouse; no re-instrumentation
- Rollback: drop the container; Langfuse data untouched
- Verdict: REJECT until the Langfuse keep/retire decision [F007] resolves — adding a viz layer on top of an incumbent still under trial is premature; revisit if Langfuse is kept.

## Withheld by the validator
- REC 0 (Keep the incumbent — Langfuse v4 on CT112): no candidate source URL; number(s) 15 next to [F011], [F012], [F013], [F014], [F015], [F016], [F021], [F046], [F054], [F055] are not in those fact lines
- REC 1 (OpenLIT): number(s) 3 next to [F021], [F046] are not in those fact lines

## What I could not establish
- OpenLIT could not be validated: the sandbox pulled openlit/backend:openlit-2.1.0 which does not exist on Docker Hub (run ts-20260914-230927-e882); the official image is ghcr.io/openlit/openlit:latest [compose file], so actual OpenLIT behaviour/runtime footprint is unmeasured.
- The OpenLIT ClickHouse companion run (ts-20260914-230938-13d6, clickhouse/clickhouse-server:25.12, image_size_mb 235.3, pull_s 13.7) was unhealthy/core-dumped under a 512 MB cap — whether this reflects the cap or a genuinely problematic 25.12 build is unresolved; the estate runs the same 25.12 image healthy at 518.5 MB [F011], so close to the sandbox cap.
- SigNoz minimum-RAM figure (4 GB) could not be re-fetched this run — https://docs.signoz.io/docs/install/docker/ returned a blocked-network error; carried from phase-1 notes unconfirmed.
- Exact OpenLIT runtime memory under a co-located ClickHouse is speculative; image sizes not measured.
- Whether Grafana's ClickHouse data-source plugin can be provisioned without a download at first connect (sandbox is no-egress).
- No current release-version URL for HyperDX (no clean release tag fetchable); activity evidenced only via repo commit date and CISA bulletin version numbers.


---
Fact pack: `/root/.hermes/toolsmith/runs/20260914-2305/facts.txt` · raw model report: `/root/.hermes/toolsmith/runs/20260914-2305/report.raw.md` · validator: `/root/.hermes/toolsmith/runs/20260914-2305/validated.json`
