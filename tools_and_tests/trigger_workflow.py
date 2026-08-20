import urllib.request
import json
import time
import sys

if sys.platform.startswith('win'):
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

token = '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'
repo = 'DoDuekChill/al-sangmoo-quant-bot'
workflow_file = 'daily_al_sangmoo_briefing.yml'

url = f'https://api.github.com/repos/{repo}/actions/workflows/{workflow_file}/dispatches'
data = json.dumps({"ref": "main"}).encode('utf-8')

req = urllib.request.Request(url, data=data, headers={
    'Authorization': f'Bearer {token}',
    'Accept': 'application/vnd.github+json',
    'User-Agent': 'Antigravity-Agent',
    'Content-Type': 'application/json'
})

print(f"🚀 Triggering new GitHub Actions workflow on 'main' ({workflow_file})...")
try:
    with urllib.request.urlopen(req) as res:
        print(f"✅ Workflow dispatch triggered successfully (Status: {res.status})")
except Exception as e:
    print(f"❌ Failed to trigger: {e}")
