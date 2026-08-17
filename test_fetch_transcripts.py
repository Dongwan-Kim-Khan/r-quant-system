import json
import os
import sys
from youtube_transcript_api import YouTubeTranscriptApi
import subprocess

def test_download():
    with open("rsangmoo_live_videos.json", "r", encoding="utf-8") as f:
        videos = json.load(f)
    
    os.makedirs("al_sangmoo_transcripts", exist_ok=True)
    
    success_count = 0
    print(f"Total videos to check: {len(videos)}")
    
    for i, v in enumerate(videos):
        v_id = v["id"]
        title = v["title"]
        out_path = os.path.join("al_sangmoo_transcripts", f"{i+1:02d}_{v_id}.txt")
        
        # Clean title for filename safe
        clean_title = "".join([c for c in title if c.isalnum() or c in (' ', '_', '-', '(', ')', '[', ']')]).strip()
        out_path = os.path.join("al_sangmoo_transcripts", f"{i+1:02d}_{clean_title}_{v_id}.txt")
        
        if os.path.exists(out_path):
            continue
            
        print(f"[{i+1}/{len(videos)}] Fetching transcript for: {title} ({v_id})...")
        
        try:
            # Try fetching Korean transcript
            transcript = YouTubeTranscriptApi.get_transcript(v_id, languages=['ko', 'ko-KR'])
            lines = [item['text'] for item in transcript]
            full_text = " ".join(lines)
            
            with open(out_path, "w", encoding="utf-8") as f_out:
                f_out.write(f"TITLE: {title}\nURL: {v['url']}\n\n" + full_text)
            
            print(f"  -> SUCCESS ({len(lines)} lines, {len(full_text)} chars)")
            success_count += 1
        except Exception as e:
            # If transcript-api fails, try yt-dlp auto subtitle extraction
            try:
                sub_cmd = [
                    sys.executable, "-m", "yt_dlp",
                    "--write-auto-sub",
                    "--sub-lang", "ko",
                    "--skip-download",
                    "--sub-format", "vtt/srt/best",
                    "-o", f"al_sangmoo_transcripts/{i+1:02d}_{v_id}.%(ext)s",
                    v["url"]
                ]
                res = subprocess.run(sub_cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
                if res.returncode == 0:
                    print(f"  -> SUCCESS via yt-dlp auto-sub")
                    success_count += 1
                else:
                    print(f"  -> No subtitle available ({e})")
            except Exception as e2:
                print(f"  -> Failed ({e})")
                
        if i >= 4: # Test first 5 for now
            break
            
    print(f"\nDone testing 5 videos! Success count: {success_count}")

if __name__ == "__main__":
    test_download()
