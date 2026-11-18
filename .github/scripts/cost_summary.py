"""Print a Markdown run summary from cost_report.json and the translation data to stdout."""
import json
import tomllib
from pathlib import Path

REPORT_PATH = Path(".github/log/cost_report.json")
CONFIG_PATH = Path(".github/config/config.toml")
DATA_DIR = Path("data")
BAR_WIDTH = 24


def bar(done, total):
    share = done / total if total else 0
    filled = round(share * BAR_WIDTH)
    return f"{'█' * filled}{'░' * (BAR_WIDTH - filled)} {share:6.1%}"


def coverage(languages):
    """Count mod source entries and, per language, the ones that already have a translation."""
    total = 0
    done = dict.fromkeys(languages, 0)
    for path in sorted(DATA_DIR.glob("[!_]*.toml")):
        try:
            entries = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for entry in entries.values():
            if not isinstance(entry, dict) or not str(entry.get("raw") or "").strip():
                continue
            total += 1
            for language in languages:
                done[language] += bool(entry.get(language))
    return total, done


def print_coverage():
    languages = tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8"))["languages"]["supported"]
    total, done = coverage(languages)
    if not total:
        return
    translated = sum(done.values())
    print("### Mod translation coverage")
    print("```text")
    print(f"{'All':<6}{bar(translated, total * len(languages))}  {translated:>9,} / {total * len(languages):,}")
    for language in sorted(languages, key=lambda code: -done[code]):
        print(f"{language:<6}{bar(done[language], total)}  {total - done[language]:>9,} pending")
    print("```")
    print("")


def print_run(data):
    cost = data.get("key_usage_delta_usd")
    requests = data.get("request_count", 0)
    succeeded = data.get("success_count", 0)
    unresolved = data.get("unresolved_translation_count", 0)
    deferred = data.get("quota_deferred_count", 0)
    if data.get("gateway_quota_exhausted"):
        print("**Stopped early:** gateway quota exhausted; untranslated entries stay pending for the next run")
        print("")
    print("| Translated | Unresolved | Deferred | Cost |")
    print("|---:|---:|---:|---:|")
    print(f"| {succeeded:,} | {unresolved:,} | {deferred:,} | "
          f"{f'${cost:.4f}' if cost is not None else 'unavailable'} |")
    print("")
    print("### This run")
    print("```text")
    print(f"{'Requests succeeded':<20}{bar(succeeded, requests)}  {succeeded:,} / {requests:,}")
    print(f"{'Failures recovered':<20}{bar(data.get('recovered_fail_count', 0), data.get('fail_count', 0))}  "
          f"{data.get('recovered_fail_count', 0):,} / {data.get('fail_count', 0):,}")
    print(f"{'Input from cache':<20}{bar(data.get('cached_tokens', 0), data.get('input_tokens', 0))}  "
          f"{data.get('cached_tokens', 0):,} / {data.get('input_tokens', 0):,} tokens")
    print(f"{'Output on reasoning':<20}{bar(data.get('reasoning_tokens', 0), data.get('output_tokens', 0))}  "
          f"{data.get('reasoning_tokens', 0):,} / {data.get('output_tokens', 0):,} tokens")
    print("```")
    print("")


def print_details(data):
    print("<details><summary>Details</summary>")
    print("")
    print(f"- Model: `{data.get('model', 'unknown')}` at `{data.get('endpoint', 'unknown')}`, "
          f"reasoning effort `{data.get('reasoning_effort', 'unknown')}` (the request sets none)")
    print(f"- Session ID: `{data.get('session_id', 'unknown')}`")
    rejections = data.get("rate_limit_rejections") or {}
    if rejections:
        print("- HTTP 429 by reason: " + ", ".join(f"`{reason}` × {count:,}" for reason, count in rejections.items()))
    print(f"- Requests with unknown cost: {data.get('unknown_cost_requests', 0):,}")
    print(f"- Ordinary input tokens: {data.get('ordinary_input_tokens', 0):,}")
    print(f"- Cache write tokens: {data.get('cache_write_tokens', 0):,}")
    print(f"- Total tokens: {data.get('total_tokens', 0):,}")
    print("- Cost is the gateway key usage delta; it may include request reservations until the server reconciles.")
    print("")
    print("</details>")
    print("")


try:
    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    print_run(report)
except Exception as error:
    report = None
    print(f"⚠️ Failed to parse cost report: {error}")
    print("")
try:
    print_coverage()
except Exception as error:
    print(f"⚠️ Failed to read translation coverage: {error}")
    print("")
if report is not None:
    print_details(report)
