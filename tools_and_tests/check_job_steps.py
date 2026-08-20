import urllib.request
import json
import sys

if sys.platform.startswith('win'):
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

token = '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'
repo = 'DoDuekChill/al-sangmoo-quant-bot'
run_id = 32095532217

req = urllib.request.Request(f'https://api.github.com/repos/{repo}/actions/runs/{run_id}/jobs', headers={
    'Authorization': f'Bearer {token}',
    'Accept': 'application/vnd.github+json',
    'User-Agent': 'Antigravity-Agent'
})

with urllib.request.urlopen(req) as res:
    data = json.loads(res.read().decode('utf-8'))
    for job in data.get('jobs', []):
        print(f"Job: {job['name']} | Status: {job['status']} | Conclusion: {job['conclusion']}")
        for s in job.get('steps', []):
            print(f"  • [{s['status']}] {s['name']} -> {s['conclusion']}")
