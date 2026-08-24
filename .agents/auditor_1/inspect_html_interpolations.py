import re
import os

with open(r"d:\코딩\Playground\al_sangmoo_project\al_sangmoo_dashboard.html", "r", encoding="utf-8") as f:
    html = f.read()

script_start = html.find("<script>")
script_end = html.rfind("</script>")
js_content = html[script_start:script_end]

interpolations = re.findall(r"\$\{([^}]+)\}", js_content)
print(f"Total interpolations in script: {len(interpolations)}")

for idx, interp in enumerate(interpolations):
    clean = interp.strip()
    # Check if this interpolation handles user/server data strings without escapeHtml
    if any(k in clean for k in ["item.name", "item.ticker", "item.sector", "h.exit_advice", "h.ticker", "h.macro_stance", "x.ticker", "safe"]):
        print(f"[{idx}] Data interpolation: {clean}")
