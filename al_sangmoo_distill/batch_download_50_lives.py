import json
import os
import sys
import subprocess
import re

BASE_DIR = r"D:\코딩\Playground\al_sangmoo_distill"
RAW_SUB_DIR = os.path.join(BASE_DIR, "raw_subtitles")
MD_OUTPUT_DIR = os.path.join(BASE_DIR, "video_markdowns")

os.makedirs(RAW_SUB_DIR, exist_ok=True)
os.makedirs(MD_OUTPUT_DIR, exist_ok=True)

def vtt_to_clean_text(vtt_path):
    with open(vtt_path, 'r', encoding='utf-8', errors='ignore') as f:
        lines = f.readlines()
        
    seen = set()
    cleaned_lines = []
    
    for line in lines:
        line = line.strip()
        # Skip VTT metadata and timestamp lines
        if not line or line.startswith("WEBVTT") or line.startswith("Kind:") or line.startswith("Language:") or "-->" in line:
            continue
        # Remove HTML-like tags like <c> </c> <00:00:00>
        clean = re.sub(r'<[^>]+>', '', line).strip()
        if not clean:
            continue
        # Avoid simple duplicate line repeats common in auto-subtitles
        if clean not in seen:
            seen.add(clean)
            cleaned_lines.append(clean)
            if len(seen) > 30: # Rolling set to avoid memory growth and allow natural phrase recurrence later
                seen.pop()
                
    return " ".join(cleaned_lines)

def download_and_process_all():
    json_path = r"D:\코딩\Playground\rsangmoo_live_videos.json"
    with open(json_path, 'r', encoding='utf-8') as f:
        videos = json.load(f)
        
    print(f"Total videos to process: {len(videos)}")
    
    summary_list = []
    
    for idx, v in enumerate(videos):
        vid_id = v["id"]
        title = v["title"]
        url = v["url"]
        prefix = f"{idx+1:02d}"
        
        # Clean title for filename
        clean_title = "".join([c for c in title if c.isalnum() or c in (' ', '_', '-', '(', ')', '[', ']')]).strip()
        clean_title = re.sub(r'\s+', '_', clean_title)
        
        md_filename = f"{prefix}_{clean_title}_{vid_id}.md"
        md_path = os.path.join(MD_OUTPUT_DIR, md_filename)
        vtt_output_template = os.path.join(RAW_SUB_DIR, f"{prefix}_{vid_id}")
        
        print(f"\n[{idx+1}/{len(videos)}] Processing: {title} ({vid_id})")
        
        # Check if MD already exists
        if os.path.exists(md_path) and os.path.getsize(md_path) > 500:
            print(f"  -> Already completed: {md_filename}")
            summary_list.append({"index": idx+1, "id": vid_id, "title": title, "status": "EXISTS", "path": md_path})
            continue
            
        # Download subtitle using yt-dlp
        vtt_file = None
        # Check if vtt file already in raw_subtitles
        expected_vtt = f"{vtt_output_template}.ko.vtt"
        if not os.path.exists(expected_vtt):
            cmd = [
                sys.executable, "-m", "yt_dlp",
                "--write-auto-sub",
                "--sub-lang", "ko",
                "--skip-download",
                "--sub-format", "vtt",
                "-o", f"{vtt_output_template}.%(ext)s",
                url
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
            
        if os.path.exists(expected_vtt):
            vtt_file = expected_vtt
            
        if not vtt_file or not os.path.exists(vtt_file):
            print(f"  -> No subtitle found for {vid_id}")
            # Still create MD with metadata
            md_content = f"""# [{idx+1:02d}] {title}

- **URL**: [{url}]({url})
- **Video ID**: `{vid_id}`
- **상태**: 실시간 방송 중이거나 자막 생성 대기 중인 영상

*(해당 영상은 아직 자동 자막이 생성되지 않았거나 실시간 스트리밍 중인 영상입니다)*
"""
            with open(md_path, 'w', encoding='utf-8') as f_out:
                f_out.write(md_content)
            summary_list.append({"index": idx+1, "id": vid_id, "title": title, "status": "NO_SUBTITLE", "path": md_path})
            continue
            
        # Parse VTT
        full_transcript = vtt_to_clean_text(vtt_file)
        char_count = len(full_transcript)
        word_count = len(full_transcript.split())
        
        # Categorize content based on title
        category = "일반 시황"
        if "지표추적자" in title:
            category = "지표추적자 (기술적 분석 / 지표 스터디)"
        elif "당일전략" in title:
            category = "당일전략 (장전 수급 및 당일 매매 타점)"
        elif "작전타임" in title:
            category = "작전타임 (장중 긴급 변동성 대응)"
        elif "Trading" in title or "Station" in title or "Room" in title:
            category = "R's Trading Station (매크로 / 주간 결산)"
        elif "rADIO" in title or "영화" in title:
            category = "rADIO (심야 라디오 / 인문학 및 인생관)"
            
        md_content = f"""# [{idx+1:02d}] {title}

- **구분/코너**: `{category}`
- **URL**: [{url}]({url})
- **Video ID**: `{vid_id}`
- **텍스트 규모**: 약 {char_count:,}자 ({word_count:,} 단어)

---

## 1. 영상 개요 & 방송 맥락
* **제목**: {title}
* **주요 타겟**: {category}

---

## 2. 전체 자막 스크립트 전문 (Transcript)

{full_transcript}
"""
        with open(md_path, 'w', encoding='utf-8') as f_out:
            f_out.write(md_content)
            
        print(f"  -> SUCCESS! Created MD ({char_count:,} chars) -> {md_filename}")
        summary_list.append({"index": idx+1, "id": vid_id, "title": title, "status": "SUCCESS", "chars": char_count, "path": md_path})

    # Save master summary
    summary_path = os.path.join(BASE_DIR, "download_summary.json")
    with open(summary_path, 'w', encoding='utf-8') as f:
        json.dump(summary_list, f, ensure_ascii=False, indent=2)
        
    print(f"\n==========================================")
    print(f"ALL 50 VIDEOS PROCESSED! Results saved to {BASE_DIR}")
    print(f"==========================================")

if __name__ == "__main__":
    download_and_process_all()
