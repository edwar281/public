"""05_build_dashboard.py: inject results into dashboard/template.html -> dashboard/index.html (self-contained)."""
import json
o = json.load(open("out/ookla.json")); a = json.load(open("out/anomalies.json"))
a.pop("z_hist", None)
o["anom"] = a
body = open("dashboard/template.html").read().replace("__DATA__", json.dumps(o, separators=(",", ":")))
html = ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
        '<meta name="description" content="Seven years of US broadband speed tests by county: the rural gap, what explains it, and outage detection, by Will Edwards, PhD.">\n'
        '</head>\n<body>\n' + body + '\n</body>\n</html>\n')
open("dashboard/index.html", "w").write(html)
print("wrote dashboard/index.html", len(html) // 1024, "KB")
