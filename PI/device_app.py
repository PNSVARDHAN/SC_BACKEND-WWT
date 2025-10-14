import os
import json
import time
import requests
import platform
import subprocess
import socket
from datetime import datetime, timezone, timedelta

# ---------------- CONFIG -------------------------------
# Path to config file
CONFIG_PATH = os.path.join(os.getcwd(), "config.json")

# Ensure config file exists
if not os.path.exists(CONFIG_PATH):
    raise FileNotFoundError(f"Config file not found: {CONFIG_PATH}")

# Load config
with open(CONFIG_PATH, "r") as f:
    config = json.load(f)

# Assign variables from config
API_BASE = config.get("backend_url", "")      # matches your Flask config key
DEVICE_TOKEN = config.get("device_token", "") # matches your Flask config key
DEVICE_CODE = config.get("device_code", "")   # optional, for reference
VIDEO_DIR = os.path.join(os.getcwd(), "videos_pi")

#--------------------___________________-----------------------
CHECK_INTERVAL = 10  # Check every 10 seconds for schedule switch
PREFETCH_HOURS = 12  # Prefetch videos for next 12 hours
VLC_RC_PORT = 9999   # RC interface port for Pi/Linux
# ------------------------------------------------------

os.makedirs(VIDEO_DIR, exist_ok=True)
IST = timezone(timedelta(hours=5, minutes=30))
SYSTEM = platform.system()
IS_WINDOWS = SYSTEM == "Windows"

vlc_process = None
current_video_list = []
current_index = 0
current_schedule_id = None

# ---------------- VLC Launch --------------------------
def start_vlc(default_video_path):
    global vlc_process
    if IS_WINDOWS:
        vlc_path = r"C:\Program Files\VideoLAN\VLC\vlc.exe"
        if not os.path.exists(vlc_path):
            raise RuntimeError("VLC not found on Windows.")
        vlc_process = subprocess.Popen([vlc_path, default_video_path, "--loop", "--fullscreen"])
        print(f"[INFO] VLC started on Windows: looping default video")
    else:
        vlc_process = subprocess.Popen([
            "cvlc",
            default_video_path,
            "--loop",
            "--intf", "dummy",
            "--extraintf", "rc",
            f"--rc-host=localhost:{VLC_RC_PORT}"
        ])
        print(f"[INFO] VLC started on Pi/Linux with RC interface")

def send_vlc_command(cmd):
    if IS_WINDOWS:
        return
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.connect(("localhost", VLC_RC_PORT))
            s.sendall((cmd + "\n").encode())
    except Exception as e:
        print(f"[ERROR] VLC command failed: {e}")

# ---------------- Fetch & Download --------------------
def fetch_schedules():
    try:
        resp = requests.post(f"{API_BASE}/api/devices/fetch-schedules",
                             json={"device_token": DEVICE_TOKEN}, timeout=10)
        resp.raise_for_status()
        return resp.json().get("schedules", [])
    except Exception as e:
        print(f"[ERROR] Fetch schedules failed: {e}")
        return []

def safe_filename(title, video_id):
    base = "".join(c for c in title if c.isalnum() or c in (' ', '_')).rstrip()
    return f"{base}_{video_id}.mp4"

def download_video(video_id, video_url, title):
    local_path = os.path.join(VIDEO_DIR, safe_filename(title, video_id))
    if os.path.exists(local_path):
        return local_path
    try:
        print(f"[DOWNLOADING] {title} from {video_url}")
        resp = requests.get(video_url, stream=True, timeout=60)
        resp.raise_for_status()
        with open(local_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        print(f"[OK] Saved to {local_path}")
        return local_path
    except Exception as e:
        print(f"[ERROR] Failed to download {title}: {e}")
        return None

def update_download_status(video_id, schedule_group_id):
    try:
        resp = requests.post(f"{API_BASE}/api/devices/update-download-status",
                             json={"device_token": DEVICE_TOKEN,
                                   "video_id": video_id,
                                   "schedule_group_id": schedule_group_id},
                             timeout=10)
        resp.raise_for_status()
    except Exception as e:
        print(f"[ERROR] Update status failed: {e}")

def save_schedules_json(schedules):
    json_path = os.path.join(VIDEO_DIR, "schedules.json")
    with open(json_path, "w") as f:
        json.dump(schedules, f, indent=4)

# ---------------- Default Video ------------------------
def fetch_default_video():
    try:
        resp = requests.get(f"{API_BASE}/api/videos/default-video", timeout=10)
        resp.raise_for_status()
        data = resp.json()
        video_id = data["video_id"]
        title = data["title"]
        video_link = data["video_link"]
        local_file = os.path.join(VIDEO_DIR, safe_filename(title, video_id))
        if not os.path.exists(local_file):
            download_video(video_id, video_link, title)
        return local_file
    except Exception as e:
        print(f"[ERROR] No default video found: {e}")
        return None

# ---------------- Video Playback -----------------------
def playback_schedule(schedule, default_video_path):
    global current_video_list, current_index, current_schedule_id

    now = datetime.now(IST)
    active_videos = []
    schedule_id = None

    for sch in schedule:
        start_time = datetime.fromisoformat(sch['start_time']).replace(tzinfo=IST)
        end_time = datetime.fromisoformat(sch['end_time']).replace(tzinfo=IST) if sch['end_time'] else None
        if start_time <= now <= (end_time or now + timedelta(days=1)):
            schedule_id = sch['schedule_id']
            for v in sch['videos']:
                video_file = os.path.join(VIDEO_DIR, safe_filename(v['title'], int(v['video_id'])))
                if os.path.exists(video_file):
                    active_videos.append(video_file)
            break

    if not active_videos:
        # No schedule active, play default
        if current_video_list != [default_video_path]:
            print(f"[PLAYING DEFAULT] {default_video_path}")
            if IS_WINDOWS:
                subprocess.Popen([r"C:\Program Files\VideoLAN\VLC\vlc.exe", default_video_path, "--loop", "--fullscreen"])
            else:
                send_vlc_command("clear")
                send_vlc_command(f"add {default_video_path}")
                send_vlc_command("loop")
            current_video_list = [default_video_path]
            current_index = 0
            current_schedule_id = None
        return

    # Play scheduled videos if changed
    if schedule_id != current_schedule_id or current_video_list != active_videos:
        print(f"[PLAYING SCHEDULE {schedule_id}] Videos: {active_videos}")
        if IS_WINDOWS:
            # Windows: play first video in loop
            subprocess.Popen([r"C:\Program Files\VideoLAN\VLC\vlc.exe", active_videos[0], "--loop", "--fullscreen"])
        else:
            send_vlc_command("clear")
            for v in active_videos:
                send_vlc_command(f"add {v}")
            send_vlc_command("loop")
        current_video_list = active_videos
        current_index = 0
        current_schedule_id = schedule_id


#-----------------------status update -----------------------
def get_current_vlc_media():
    """Ask VLC RC interface for the current playing file name."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.connect(("localhost", VLC_RC_PORT))
            s.sendall(b"status\n")
            data = s.recv(4096).decode(errors="ignore")

            for line in data.splitlines():
                if "input:" in line:
                    file_path = line.split("input:")[-1].strip()
                    if file_path.startswith("file://"):
                        file_path = file_path.replace("file://", "")
                    return os.path.basename(file_path)
    except Exception as e:
        print(f"[ERROR] Could not get VLC status: {e}")
    return None


def send_playback_update(current_file):
    """Send currently playing video info to backend."""
    if not current_file:
        print("[WARN] No current file detected, skipping playback update.")
        return

    video_id = None

    # Try to find matching video_id from known files
    for v in current_video_list:
        if os.path.basename(v) == current_file:
            try:
                video_id = int(v.split("_")[-1].split(".")[0])  
            except ValueError:
                print(f"[WARN] Could not parse video_id from file: {v}")
            break

    payload = {
        "device_token": DEVICE_TOKEN,
        "video_id": video_id,
        "playback_state": "playing" if video_id else "stopped"
    }

    try:
        resp = requests.post(f"{API_BASE}/api/devices/update-playback", json=payload, timeout=5)
        if resp.status_code == 200:
            print(f"[SYNC OK] Reported playback → video_id={video_id}, file={current_file}")
        else:
            print(f"[SYNC FAIL] Server responded {resp.status_code}: {resp.text}")
    except requests.RequestException as e:
        print(f"[ERROR] Playback update failed: {e}")


# ---------------- Main Loop ---------------------------
def main():
    print("=== Raspberry Pi / Windows Video Scheduler ===")
    default_video_path = fetch_default_video()
    if not default_video_path:
        print("[ERROR] Default video is required. Exiting.")
        return

    start_vlc(default_video_path)

    while True:
        now = datetime.now(IST)
        schedules = fetch_schedules()
        if schedules:
            for sch in schedules:
                schedule_group_id = sch['schedule_group_id']
                for v in sch['videos']:
                    video_id = int(v["video_id"])
                    title = v.get("title", f"video_{video_id}")
                    video_url = v["video_link"]
                    local_path = os.path.join(VIDEO_DIR, safe_filename(title, video_id))
                    if not os.path.exists(local_path):
                        download_video(video_id, video_url, title)
                        update_download_status(video_id, schedule_group_id)
        

            save_schedules_json(schedules)

        playback_schedule(schedules, default_video_path)
        current_file = get_current_vlc_media()
        send_playback_update(current_file)

        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
