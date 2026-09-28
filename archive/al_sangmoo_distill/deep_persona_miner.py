import os
import glob
import re
import json

MD_DIR = r"D:\코딩\Playground\al_sangmoo_distill\video_markdowns"
OUT_PERSONA_SPEC = r"D:\코딩\Playground\al_sangmoo_distill\al_sangmoo_digital_twin_architecture.md"

def mine_deep_persona():
    files = sorted(glob.glob(os.path.join(MD_DIR, "*.md")))
    
    # 1. Market Cycle & Macro Worldview
    cycle_mentions = []
    # 2. Korea vs US vs Japan Geopolitics
    geo_mentions = []
    # 3. Cultural Allegories (Movies, History, Books)
    culture_mentions = []
    # 4. Hidden Psychology & Contrarian Rules
    psych_mentions = []
    
    for fpath in files:
        fname = os.path.basename(fpath)
        with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
            text = f.read()
            
        # Match Cycle
        for sentence in re.split(r'[.!?\n]', text):
            sentence = sentence.strip()
            if len(sentence) < 15 or len(sentence) > 200:
                continue
            if any(k in sentence for k in ["사이클", "주기", "국면", "사계절", "겨울", "봄이", "바닥", "고점"]):
                cycle_mentions.append((fname, sentence))
            if any(k in sentence for k in ["국장", "미장", "코스피", "엔캐리", "일본은행", "연준", "중국", "관료", "정치"]):
                geo_mentions.append((fname, sentence))
            if any(k in sentence for k in ["영화", "책", "소설", "신화", "에반게리온", "마진콜", "빅쇼트", "역사"]):
                culture_mentions.append((fname, sentence))
            if any(k in sentence for k in ["개미", "대중", "광기", "탐욕", "공포", "멘탈", "생존", "잃지"]):
                psych_mentions.append((fname, sentence))
                
    print(f"Cycle mentions: {len(cycle_mentions)}")
    print(f"Geo/Macro mentions: {len(geo_mentions)}")
    print(f"Culture mentions: {len(culture_mentions)}")
    print(f"Psychology mentions: {len(psych_mentions)}")
    
    return cycle_mentions, geo_mentions, culture_mentions, psych_mentions

if __name__ == "__main__":
    c, g, cu, p = mine_deep_persona()
