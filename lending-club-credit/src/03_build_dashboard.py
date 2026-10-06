"""03_build_dashboard.py: inject results into dashboard/template.html -> dashboard/index.html (self-contained)."""
import json
d = {"loans": json.load(open("out/loans.json")), "model": json.load(open("out/model.json"))}
d["loans"].pop("by_year_grade", None)
body = open("dashboard/template.html").read().replace("__DATA__", json.dumps(d, separators=(",", ":")))
html = ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
        '<meta name="description" content="Credit losses, realized investor returns and a profit-based approval policy on 2.26M Lending Club loans, by Will Edwards, PhD.">\n'
        '</head>\n<body>\n' + body + '\n</body>\n</html>\n')
open("dashboard/index.html", "w").write(html)
print("wrote dashboard/index.html", len(html) // 1024, "KB")
