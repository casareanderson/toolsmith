# Withheld by toolsmith_validate.py — run 20260917-1030

> Published excerpt. The withhold reasons are verbatim. The REC bodies described the
> author's live ingress set-up in detail, so for publication each body is cut down to the
> line that caused a withhold.

## Summary withheld
- number(s) 24 next to [F012], [F013] are not in those fact lines
- number(s) 01 next to [F049], [F074] are not in those fact lines

The two summary sentences involved:

```
Two dead `cloudflared-web` containers (~24 MB combined) serve zero function [F012][F013].
... handling five critical vhosts with Authelia forward_auth and CF DNS-01 module compiled in [F049][F074].
```

[F012] says 10.9 MB and [F013] says 13.3 MB. The model added them up, which the SOUL forbids,
so withholding the 24 was correct. The "01" is from "DNS-01", a protocol name and not a
measurement. That one is a false positive.

## REC 1: Keep & slim (decommission cloudflared-web, migrate off NPMplus, kill npmplus after migration)
Reasons:
- URL not fetched this run: https://github.com/ZoeyVid/NPMPlus/blob/main/LICENSE
- licence has no valid class (OSI | source-available | non-commercial | unknown)
- no candidate source URL
- uncited measurement '0 MB' (no [F###], URL or run_id on that line)

```
- Candidate: Keep existing stack; remove dead containers; ... decommission NPMplus — source: phase-1 analysis
- Licence: npmplus (zoeyvid) — GNU AGPL v3 [source: https://github.com/ZoeyVid/NPMPlus/blob/main/LICENSE]; cloudflared — Apache-2.0 [source: ...]
- Resources: npmplus 79 MB compressed Docker image vs target nas rootfs 0 MB free of 1664 MB [F037]; ...
```

## REC 2: Caddy (expand existing deployment on CT100)
Reasons:
- number(s) 2.0 next to [F002], [F006], [F027] are not in those fact lines

```
- Verdict: ADOPT-TRIAL — expands what already works, eliminates dual ingress and the nas SPOF in one move,
  Apache-2.0 licence fits standing preferences [F002][F006], and CT100 has headroom [F027]. ...
```

The "2.0" is the "2.0" in "Apache-2.0". This is a false positive, and it blocked the only ADOPT-TRIAL of the run.

## REC 3: Traefik v3 (replace both NPMplus and Caddy)
Reasons:
- incumbent pain carries no [F###] fact id

```
- Incumbent: Caddy on CT100 + npmplus on nas — why: same pains as above plus introducing a second proxy adds operational complexity for no gain
```
