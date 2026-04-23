import json
from pathlib import Path
p = Path("validation_artifacts/multi_era_backtest_schwab_only_progress.json")
if not p.exists():
    print("no progress file yet"); raise SystemExit(0)
d = json.loads(p.read_text(encoding="utf-8"))
print("run_id:", d.get("run_id"))
print("status:", d.get("status"))
print("completed:", d.get("completed_count"), "/", d.get("total_eras"))
print("current_era:", d.get("current_era"))
for k, v in (d.get("era_state") or {}).items():
    print(f"  {k}: {v.get('completed_chunks', 0)}/{v.get('total_chunks', 0)}")
