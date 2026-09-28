import urllib.request
import json

import os
token = os.environ.get('GITHUB_TOKEN', '')
req = urllib.request.Request('https://api.github.com/repos/DoDuekChill/r-quant-system/actions/runs', headers={
    'Authorization': f'Bearer {token}',
    'Accept': 'application/vnd.github+json',
    'User-Agent': 'Antigravity-Agent'
})

try:
    with urllib.request.urlopen(req) as res:
        data = json.loads(res.read().decode('utf-8'))
        runs = data.get('workflow_runs', [])
        print(f"Total runs: {len(runs)}")
        for r in runs[:5]:
            msg = r.get('head_commit', {}).get('message', '').split('\n')[0]
            print(f"Run ID: {r['id']} | Status: {r['status']} | Conclusion: {r['conclusion']} | SHA: {r['head_sha'][:7]} | Message: {msg}")
except Exception as e:
    print("Error querying runs:", e)
