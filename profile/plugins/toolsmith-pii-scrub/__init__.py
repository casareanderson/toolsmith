"""toolsmith-pii-scrub — rewrite web tool results so third-party contact details never reach the LLM.

Why (measured 2026-09-14): toolsmith's first review run had its OpenRouter requests rejected
with HTTP 403 "Request blocked: PII detected (invalid_json_after_redaction)" on qwen3.7-flash AND
deepseek-v4-flash as soon as web_search results joined the context; the fallback chain then ran
the whole research phase on local qwen3.5-64k at 45-156 s per call. The fact pack alone passed.
Research needs licence texts and release dates, never anyone's e-mail or phone number, so they
are replaced with placeholders. Only web_* results are touched.
"""
import re

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONES = [
    re.compile(r"(?<![\w/.-])\+\d[\d ().-]{7,}\d"),
    re.compile(r"(?<![\w/.-])\(\d{3}\)\s?\d{3}[-. ]\d{4}\b"),
    re.compile(r"(?<![\w/.-])\d{3}[-. ]\d{3}[-. ]\d{4}\b"),
    re.compile(r"(?<![\w/.-])0\d{3,4}[ ]\d{3}[ ]?\d{3,4}\b"),
]


def scrub(tool_name=None, result=None, **kwargs):
    if not isinstance(result, str) or not (tool_name or "").startswith("web_"):
        return None
    out = EMAIL.sub("[email removed]", result)
    for p in PHONES:
        out = p.sub("[phone removed]", out)
    return out if out != result else None


def register(ctx):
    ctx.register_hook("transform_tool_result", scrub)
