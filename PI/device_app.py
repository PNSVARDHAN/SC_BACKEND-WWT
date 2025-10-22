import os
import json
import time
import requests
import platform
import subprocess
from datetime import datetime, timezone, timedelta

# ---------------- CONFIG -------------------------------
CONFIG_PATH = os.path.join(os.getcwd(), "config.json")

if not os.path.exists(CONFIG_PATH):
    raise FileNotFoundError(f"Config file not found: {CONFIG_PATH}")

with open(CONFIG_PATH, "r") as f:
    config = json.load(f)

API_BASE = config.get("backend_url", "")
DEVICE_TOKEN = config.get("device_token", "")
VIDEO_DIR = os.path.join(os.getcwd(), "videos_pi")
os.makedirs(VIDEO_DIR, exist_ok=True)

CHECK_INTERVAL = 5  # seconds
IST = timezone(timedelta(hours=5, minutes=30))
IS_WINDOWS = platform.system() == "Windows"

vlc_process = None
current_video = None

# ---------------- Helpers ----------------------------
def safe_filename(title, video_id):
    base = "".join(c for c in title if c.isalnum() or c in (' ', '_')).rstrip()
    return os.path.join(VIDEO_DIR, f"{base}_{video_id}.mp4")

def download_video(video_id, video_url, title):
    local_path = safe_filename(title, video_id)
    if os.path.exists(local_path):
        return local_path
    print(f"[DOWNLOADING] {title}")
    resp = requests.get(video_url, stream=True, timeout=60)
    resp.raise_for_status()
    with open(local_path, "wb") as f:
        for chunk in resp.iter_content(8192):
            f.write(chunk)
    print(f"[OK] Saved {local_path}")
    return local_path

def fetch_default_video():
    resp = requests.get(f"{API_BASE}/api/videos/default-video", timeout=10)
    resp.raise_for_status()
    data = resp.json()
    return download_video(data["video_id"], data["video_link"], data["title"])

def fetch_schedules():
    resp = requests.post(f"{API_BASE}/api/devices/fetch-schedules", json={"device_token": DEVICE_TOKEN}, timeout=10)
    resp.raise_for_status()
    return resp.json().get("schedules", [])

def play_video(path):
    global vlc_process
    if vlc_process:
        vlc_process.terminate()
    if IS_WINDOWS:
        vlc_path = r"C:\Program Files\VideoLAN\VLC\vlc.exe"
        vlc_process = subprocess.Popen([vlc_path, path, "--loop", "--fullscreen"])
    else:
        vlc_process = subprocess.Popen(["cvlc", path, "--loop", "--fullscreen"])
    print(f"[PLAYING] {path}")

# ---------------- Main Loop ---------------------------
def main():
    global current_video
    default_video_path = fetch_default_video()
    current_video = default_video_path
    play_video(default_video_path)

    while True:
        now = datetime.now(IST)
        try:
            schedules = fetch_schedules()
        except Exception as e:
            print(f"[ERROR] Fetch schedules failed: {e}")
            schedules = []

        active_video = None

        for sch in schedules:
            start_time = datetime.fromisoformat(sch['start_time']).replace(tzinfo=IST)
            end_time = datetime.fromisoformat(sch['end_time']).replace(tzinfo=IST) if sch['end_time'] else None

            # Skip completed schedules
            if end_time and now > end_time:
                continue

            # Active schedule check
            if start_time <= now <= (end_time or now):
                for v in sch['videos']:
                    active_video = download_video(int(v["video_id"]), v["video_link"], v["title"])
                    break
                if active_video:
                    break

        if active_video and active_video != current_video:
            current_video = active_video
            play_video(active_video)
        elif not active_video and current_video != default_video_path:
            current_video = default_video_path
            play_video(default_video_path)

        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
