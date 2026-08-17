import re

with open(r'C:\Users\kdw58\.gemini\antigravity\brain\81f55a5d-041f-494d-93b1-1614a50f5bc8\.system_generated\steps\754\content.md', 'r', encoding='utf-8', errors='ignore') as f:
    raw = f.read()

clean = re.sub(r'<[^>]+>', ' ', raw)
clean = re.sub(r'&nbsp;', ' ', clean)
clean = re.sub(r'&[a-zA-Z0-9#]+;', ' ', clean)
clean = re.sub(r'\s+', ' ', clean)

# Find all occurrences of 4.1.2 or 주식은 지금
matches = [m.start() for m in re.finditer(r'4\.1\.2|주식은 지금', clean)]

output_chunks = []
for m in matches:
    output_chunks.append(clean[max(0, m-50):m+2500])

with open(r'D:\코딩\Playground\al_sangmoo_extracted.txt', 'w', encoding='utf-8') as f:
    f.write('\n\n--- CHUNK ---\n\n'.join(output_chunks))

print("EXTRACTED!")
