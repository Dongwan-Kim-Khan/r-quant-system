import subprocess
import sys
import json

if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

def test_wepoll():
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--flat-playlist",
        "-J",
        "--playlist-items", "1:5",
        "https://www.youtube.com/@wepoll_original/streams"
    ]
    print("Fetching latest streams from @wepoll_original/streams...")
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    if res.returncode == 0:
        data = json.loads(res.stdout)
        entries = data.get("entries", [])
        print(f"Successfully found {len(entries)} streams:")
        for e in entries:
            print(f" • [{e.get('id')}] {e.get('title')} ({e.get('url')})")
    else:
        print("yt-dlp stderr:", res.stderr[:500])

if __name__ == "__main__":
    test_wepoll()
