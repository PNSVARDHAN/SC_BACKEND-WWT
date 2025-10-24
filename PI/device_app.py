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

SCHEDULE_FILE = os.path.join(os.getcwd(), "schedule.json")
CHECK_INTERVAL = 60  # check every minute for what to play
REFRESH_INTERVAL = 3 * 60  # refresh schedule every 5 minutes
PLAY_WINDOW_HOURS = 2
IST = timezone(timedelta(hours=5, minutes=30))
IS_WINDOWS = platform.system() == "Windows"

vlc_process = None
current_video = None
last_refresh = None

# ---------------- Helpers ----------------------------
def safe_filename(title, video_id):
    base = "".join(c for c in title if c.isalnum() or c in (' ', '_')).rstrip()
    return os.path.join(VIDEO_DIR, f"{base}_{video_id}.mp4")

def download_video(video_id, video_url, title):
    local_path = safe_filename(title, video_id)
    if os.path.exists(local_path):
        return local_path
    print(f"[DOWNLOADING] {title}")
    try:
        resp = requests.get(video_url, stream=True, timeout=60)
        resp.raise_for_status()
        with open(local_path, "wb") as f:
            for chunk in resp.iter_content(8192):
                f.write(chunk)
        print(f"[OK] Saved {local_path}")
    except Exception as e:
        print(f"[ERROR] Downloading {title}: {e}")
    return local_path

def fetch_default_video():
    try:
        resp = requests.get(f"{API_BASE}/api/videos/default-video", timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return download_video(data["video_id"], data["video_link"], data["title"])
    except Exception as e:
        print(f"[ERROR] Fetching default video: {e}")
        return None

def fetch_schedules():
    try:
        resp = requests.post(f"{API_BASE}/api/devices/fetch-schedules",
                             json={"device_token": DEVICE_TOKEN}, timeout=10)
        resp.raise_for_status()
        return resp.json().get("schedules", [])
    except Exception as e:
        print(f"[ERROR] Fetch schedules failed: {e}")
        return []

def generate_schedule_data(schedules, default_video_path):
    now = datetime.now(IST)
    end_time = now + timedelta(hours=PLAY_WINDOW_HOURS)
    timeline = {}

    # Fill next 2 hours minute by minute with default
    current = now.replace(second=0, microsecond=0)
    while current < end_time:
        timeline[current.strftime("%Y-%m-%d %H:%M")] = default_video_path
        current += timedelta(minutes=1)

    # Fill scheduled videos
    for sch in schedules:
        try:
            start_time = datetime.fromisoformat(sch["start_time"]).astimezone(IST)
            end_time = datetime.fromisoformat(sch["end_time"]).astimezone(IST)
        except Exception:
            continue

        # skip schedules outside the next 2 hours
        if end_time < now or start_time > end_time:
            continue

        for v in sch.get("videos", []):
            local_path = download_video(int(v["video_id"]), v["video_link"], v["title"])
            cur = start_time.replace(second=0, microsecond=0)
            while cur < end_time and cur.strftime("%Y-%m-%d %H:%M") in timeline:
                timeline[cur.strftime("%Y-%m-%d %H:%M")] = local_path
                cur += timedelta(minutes=1)

    with open(SCHEDULE_FILE, "w") as f:
        json.dump(timeline, f, indent=2)
    print(f"[UPDATED] schedule.json with {len(timeline)} minutes of data.")

def get_video_for_now():
    now_key = datetime.now(IST).strftime("%Y-%m-%d %H:%M")
    if not os.path.exists(SCHEDULE_FILE):
        return None
    try:
        with open(SCHEDULE_FILE, "r") as f:
            data = json.load(f)
        return data.get(now_key)
    except Exception as e:
        print(f"[ERROR] Reading schedule.json: {e}")
        return None

def play_video(path):
    global vlc_process
    if not path:
        return
    if vlc_process:
        vlc_process.terminate()
    try:
        if IS_WINDOWS:
            vlc_path = r"C:\Program Files\VideoLAN\VLC\vlc.exe"
            vlc_process = subprocess.Popen([vlc_path, path, "--fullscreen", "--loop"])
        else:
            vlc_process = subprocess.Popen(["cvlc", path, "--fullscreen", "--loop"])
        print(f"[PLAYING] {os.path.basename(path)}")
    except Exception as e:
        print(f"[ERROR] Launching VLC: {e}")

# ---------------- Main Loop ---------------------------
def main():
    global current_video, last_refresh
    print("[START] Smart Scheduler running...")

    default_video_path = fetch_default_video()
    if not default_video_path:
        print("[ERROR] Default video not found, exiting.")
        return

    # initial schedule load
    schedules = fetch_schedules()
    generate_schedule_data(schedules, default_video_path)
    last_refresh = time.time()

    current_video = get_video_for_now()
    play_video(current_video or default_video_path)

    while True:
        now = time.time()

        # refresh every 5 minutes
        if now - last_refresh >= REFRESH_INTERVAL:
            print("[REFRESH] Updating schedule.json...")
            schedules = fetch_schedules()
            generate_schedule_data(schedules, default_video_path)
            last_refresh = now

        # check current minute’s plan
        next_video = get_video_for_now()
        if next_video and next_video != current_video:
            current_video = next_video
            play_video(next_video)

        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
