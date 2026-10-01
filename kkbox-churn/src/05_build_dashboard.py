"""05_build_dashboard.py — Inject model + survival results into the dashboard template -> dashboard/index.html"""
import json
s = json.load(open("out/survival.json")); m = json.load(open("out/model.json"))
for t in s["cox"]["terms"]:
    t["p"] = float(f'{t["p"]:.2g}')
d = {"surv": {"cohort": s["cohort"], "overall": s["overall"],
              "segments": {k: v for k, v in s["segments"].items() if k != "price"},
              "hazard": s["hazard"], "cox": s["cox"], "ret12": s["retention_12m"], "trial": s["trial_funnel"]},
     "model": {"cohorts": m["cohorts"], "specs": m["specs"], "cal": m["calibration"], "lift": m["lift"],
               "capture": m["capture_at"], "shap": m["shap_top"]}}
html = open("dashboard/template.html").read().replace("__DATA__", json.dumps(d, separators=(",", ":")))
open("dashboard/index.html", "w").write(html)
print("wrote dashboard/index.html")
