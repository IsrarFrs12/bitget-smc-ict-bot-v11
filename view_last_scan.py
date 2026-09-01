import json, os
p=os.path.join("state","last_scan_report.json")
if not os.path.exists(p):
    print("No scan report yet. Run main.py first.")
    raise SystemExit(1)
with open(p,encoding="utf-8") as f:
    x=json.load(f)
print(json.dumps(x, indent=2))
