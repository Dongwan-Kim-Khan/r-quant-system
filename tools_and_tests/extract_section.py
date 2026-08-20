import re

with open(r'C:\Users\kdw58\.gemini\antigravity\brain\81f55a5d-041f-494d-93b1-1614a50f5bc8\.system_generated\steps\754\content.md', 'r', encoding='utf-8', errors='ignore') as f:
    text = f.read()

clean = re.sub(r'<[^>]+>', ' ', text)
clean = re.sub(r'&nbsp;', ' ', clean)
clean = re.sub(r'&[a-zA-Z0-9#]+;', ' ', clean)
clean = re.sub(r'\s+', ' ', clean)

# Find heading '4.1.2'
matches = [m.start() for m in re.finditer(r'4\.1\.2', clean)]
print(f"Found {len(matches)} occurrences of 4.1.2")

# Usually the TOC is first, then the actual content
for i, m in enumerate(matches):
    chunk = clean[m:m+2000]
    print(f"--- MATCH {i} ---")
    print(chunk[:300])

with open(r'D:\코딩\Playground\section_4_1_2.txt', 'w', encoding='utf-8') as f:
    for m in matches:
        f.write(f"\n\n=== MATCH ===\n\n" + clean[m:m+2500])
