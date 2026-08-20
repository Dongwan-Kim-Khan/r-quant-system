import re

with open(r'C:\Users\kdw58\.gemini\antigravity\brain\81f55a5d-041f-494d-93b1-1614a50f5bc8\.system_generated\steps\754\content.md', 'r', encoding='utf-8', errors='ignore') as f:
    text = f.read()

# Clean HTML
clean = re.sub(r'<script.*?</script>', '', text, flags=re.DOTALL)
clean = re.sub(r'<style.*?</style>', '', clean, flags=re.DOTALL)
clean = re.sub(r'<[^>]+>', ' ', clean)
clean = re.sub(r'&nbsp;', ' ', clean)
clean = re.sub(r'&[a-zA-Z0-9#]+;', ' ', clean)
clean = re.sub(r'\s+', ' ', clean)

with open(r'D:\코딩\Playground\al_sangmoo_full_clean.txt', 'w', encoding='utf-8') as f_out:
    f_out.write(clean)

print("CLEAN FULL TEXT SAVED. Length:", len(clean))
