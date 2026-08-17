import json
import os
import subprocess
import sys

def get_channel_lives():
    channel_url = "https://www.youtube.com/@rsangmoo/streams"
    output_json = "rsangmoo_live_videos.json"
    
    print(f"Fetching video list from {channel_url}...")
    
    # Use yt-dlp to get flat playlist info in JSON format
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--flat-playlist",
        "-J",
        channel_url
    ]
    
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    if result.returncode != 0:
        print("Error fetching with yt-dlp:", result.stderr)
        return []
    
    try:
        data = json.loads(result.stdout)
        entries = data.get("entries", [])
        videos = []
        for e in entries:
            v_id = e.get("id")
            title = e.get("title")
            url = f"https://www.youtube.com/watch?v={v_id}"
            videos.append({
                "id": v_id,
                "title": title,
                "url": url,
                "duration": e.get("duration"),
                "view_count": e.get("view_count")
            })
        
        with open(output_json, "w", encoding="utf-8") as f:
            json.dump(videos, f, ensure_ascii=False, indent=2)
            
        print(f"Successfully collected {len(videos)} live stream videos!")
        return videos
    except Exception as ex:
        print("Failed to parse JSON:", ex)
        return []

if __name__ == "__main__":
    vids = get_channel_lives()
    for i, v in enumerate(vids[:10]):
        print(f"{i+1}. [{v['id']}] {v['title']}")
