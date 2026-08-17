import os
import sys
import json
from datetime import datetime

# Windows encoding fix
if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

DASHBOARD_JSON = "dashboard_data.json"
DASHBOARD_HTML = "al_sangmoo_dashboard.html"

def inject_standalone_data():
    with open(DASHBOARD_JSON, "r", encoding="utf-8") as f:
        data_json_str = f.read()

    # Read template HTML
    with open(DASHBOARD_HTML, "r", encoding="utf-8") as f:
        html = f.read()

    # Replace loadData logic with inline EMBEDDED_DATA fallback
    injection = f"""
    <script>
        // Pre-embedded initial data payload (No CORS errors on file:///)
        window.EMBEDDED_DASHBOARD_DATA = {data_json_str};
    </script>
    """

    # Insert before </head>
    if "window.EMBEDDED_DASHBOARD_DATA" in html:
        # Replace existing
        import re
        html = re.sub(r'<script>\s*// Pre-embedded initial data payload.*?</script>', injection.strip(), html, flags=re.DOTALL)
    else:
        html = html.replace('</head>', f'{injection}\n</head>')

    # Update loadData function to use EMBEDDED_DASHBOARD_DATA if fetch fails
    new_load_data = """
        async function loadData() {
            try {
                // Try fetching live from local backend if available
                const res = await fetch('/api/dashboard?t=' + new Date().getTime());
                if (res.ok) {
                    const data = await res.json();
                    renderDashboard(data);
                    return;
                }
            } catch (e) {
                // Fallback to local json or embedded payload
            }

            try {
                const res = await fetch('dashboard_data.json?t=' + new Date().getTime());
                if (res.ok) {
                    const data = await res.json();
                    renderDashboard(data);
                    return;
                }
            } catch (e) {
                // Ignore CORS error on file:/// and use embedded payload
            }

            if (window.EMBEDDED_DASHBOARD_DATA) {
                renderDashboard(window.EMBEDDED_DASHBOARD_DATA);
            }
        }
    """
    
    # Replace loadData in html
    import re
    html = re.sub(r'async function loadData\(\)\s*\{.*?\}\s*\}', new_load_data.strip(), html, flags=re.DOTALL)

    with open(DASHBOARD_HTML, "w", encoding="utf-8") as f:
        f.write(html)
        
    print(f"Successfully baked live market data into {DASHBOARD_HTML}! It now opens offline on file:/// with zero CORS errors.")

if __name__ == "__main__":
    inject_standalone_data()
