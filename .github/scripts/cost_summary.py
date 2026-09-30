"""Print a Markdown cost summary from cost_report.json to stdout."""
import json

report_path = ".github/log/cost_report.json"
try:
    with open(report_path, "r") as f:
        data = json.load(f)

    print(f"**Model:** `{data.get('model', 'unknown')}`")
    print(f"**Reasoning effort:** `{data.get('reasoning_effort', 'unknown')}`")
    print(f"**Total Requests:** {data.get('request_count', 0):,}")
    print(f"**Success Rate:** {data.get('success_rate', 0):.1f}%")
    print("")
    print("### Token Usage")
    print(f"- Input tokens:  {data.get('input_tokens', 0):,}")
    print(f"- Ordinary input: {data.get('ordinary_input_tokens', 0):,}")
    print(f"- Cache reads: {data.get('cached_tokens', 0):,}")
    print(f"- Cache writes: {data.get('cache_write_tokens', 0):,}")
    print(f"- Cache hit rate: {data.get('cache_hit_rate', 0):.1%}")
    print(f"- Reasoning tokens: {data.get('reasoning_tokens', 0):,}")
    print(f"- Output tokens: {data.get('output_tokens', 0):,}")
    print(f"- Total tokens:  {data.get('total_tokens', 0):,}")
    print("")
    print("### 💵 Estimated Cost")
    cost = data.get("estimated_cost_usd", 0)
    print(f"**${cost:.4f} USD**")
    print("")

    if not data.get('cost_tracking_complete', False):
        print("⚠️ Usage accounting is incomplete; unknown requests retain their maximum budget reservation.")
    if cost > 1.0:
        print("⚠️ **Warning:** Cost exceeds $1.00 - consider reviewing translation settings")
except Exception as e:
    print(f"⚠️ Failed to parse cost report: {e}")
