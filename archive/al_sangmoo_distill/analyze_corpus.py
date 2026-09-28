import os
import json
import glob
import re

MD_DIR = r"D:\코딩\Playground\al_sangmoo_distill\video_markdowns"
OUT_DOCTRINE = r"D:\코딩\Playground\al_sangmoo_distill\al_sangmoo_investment_doctrine.md"
OUT_INDEX = r"D:\코딩\Playground\al_sangmoo_distill\video_index.md"

def analyze_all_transcripts():
    files = sorted(glob.glob(os.path.join(MD_DIR, "*.md")))
    print(f"Total markdown files found: {len(files)}")
    
    total_chars = 0
    total_words = 0
    
    category_counts = {}
    
    # Keyword tracking
    indicators = {"일목균형표": 0, "구름대": 0, "기준선": 0, "전환선": 0, "이동평균선": 0, "20일선": 0, "60일선": 0, "지지선": 0, "저항선": 0, "거래량": 0, "분홍색": 0}
    macros = {"환율": 0, "엔화": 0, "엔캐리": 0, "금리": 0, "미국채": 0, "연준": 0, "FOMC": 0, "인플레이션": 0, "유동성": 0}
    actions = {"매수": 0, "매도": 0, "손절": 0, "익절": 0, "현금": 0, "숏": 0, "인버스": 0, "헤지": 0, "비중": 0, "분할": 0}
    memes_tones = {"확정": 0, "음모론": 0, "17년": 0, "털린다": 0, "생존": 0, "조심": 0, "버티": 0}
    
    video_summaries = []
    
    for fpath in files:
        fname = os.path.basename(fpath)
        with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
            
        char_len = len(content)
        word_len = len(content.split())
        total_chars += char_len
        total_words += word_len
        
        # Extract title & category
        m_cat = re.search(r'\*\*구분/코너\*\*:\s*`([^`]+)`', content)
        cat = m_cat.group(1) if m_cat else "기타"
        category_counts[cat] = category_counts.get(cat, 0) + 1
        
        m_title = re.search(r'^#\s*\[\d+\]\s*(.*)', content, re.MULTILINE)
        title = m_title.group(1) if m_title else fname
        
        # Count keywords
        for k in indicators:
            indicators[k] += len(re.findall(re.escape(k), content))
        for k in macros:
            macros[k] += len(re.findall(re.escape(k), content))
        for k in actions:
            actions[k] += len(re.findall(re.escape(k), content))
        for k in memes_tones:
            memes_tones[k] += len(re.findall(re.escape(k), content))
            
        video_summaries.append({
            "filename": fname,
            "title": title,
            "category": cat,
            "chars": char_len,
            "words": word_len
        })
        
    print(f"Total Chars: {total_chars:,}, Total Words: {total_words:,}")
    print("Indicators:", indicators)
    print("Macros:", macros)
    print("Actions:", actions)
    print("Memes/Tones:", memes_tones)
    
    # Generate Index MD
    index_md = f"""# 📚 알상무 라이브 50편 데이터베이스 인덱스

- **총 분석 영상 수**: {len(files)}편
- **총 추출 텍스트 분량**: 약 {total_chars:,}자 ({total_words:,} 단어)
- **주요 코너별 구성**:
"""
    for cat, cnt in category_counts.items():
        index_md += f"  * **{cat}**: {cnt}편\n"
        
    index_md += """
---

## 📋 50편 영상별 상세 마크다운 목록

| 번호 | 코너 구분 | 영상 제목 | 텍스트 규모 | 파일 링크 |
| :---: | :--- | :--- | :---: | :--- |
"""
    for idx, v in enumerate(video_summaries):
        index_md += f"| {idx+1:02d} | `{v['category'].split(' ')[0]}` | {v['title']} | {v['chars']:,}자 | [{v['filename']}](file:///D:/코딩/Playground/al_sangmoo_distill/video_markdowns/{v['filename']}) |\n"
        
    with open(OUT_INDEX, 'w', encoding='utf-8') as f:
        f.write(index_md)
        
    return total_chars, total_words, category_counts, indicators, macros, actions, memes_tones

if __name__ == "__main__":
    analyze_all_transcripts()
