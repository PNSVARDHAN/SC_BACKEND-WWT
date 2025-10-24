import uuid, json
from datetime import datetime, timedelta, timezone
from utils.timezone import IST, now_ist, ensure_ist
from flask import Blueprint, jsonify, request, current_app, send_file
from flask_jwt_extended import jwt_required, get_jwt_identity, get_jwt
from models.models import Device, Video
from models.models import New_Devices
from extensions import db
from werkzeug.utils import secure_filename
import os
import io
from models.models import User

devices_bp = Blueprint('devices', __name__)

@devices_bp.route('/create', methods=['POST'])
@jwt_required()
def create_device():
    user_id = get_jwt_identity()
    data = request.json or {}
    name = data.get('name', '').strip()

    if not name:
        return jsonify({"error": "Device name is required"}), 400

    # Check for duplicate device_code
    if Device.query.filter_by(device_code=name).first():
        return jsonify({"error": "Device code already exists"}), 400

    # Sanitize device_code for filename safety
    safe_code = "".join(c for c in name if c.isalnum() or c in ('-', '_'))

    # Generate device token
    device_token = uuid.uuid4().hex

    # Create device record
    device = Device(
        device_code=safe_code,
        device_token=device_token,
        user_id=user_id,
        status='inactive'
    )
    db.session.add(device)
    db.session.commit()

    # Prepare config
    config = {
        "API_BASE": request.host_url.rstrip('/'),
        "DEVICE_NAME": safe_code,
        "DEVICE_TOKEN": device_token,
        "api_version": "1.0"
    }

    # Create config file in memory
    config_file = io.StringIO()
    json.dump(config, config_file, indent=2)
    config_file.seek(0)

    # Return JSON with device info + file download
    response = send_file(
        io.BytesIO(config_file.getvalue().encode()),
        mimetype='application/json',
        as_attachment=True,
        download_name=f'device_{safe_code}_config.json'
    )

    # Optional: include device info in headers or JSON body
    response.headers["X-Device-Id"] = str(device.device_id)
    response.headers["X-Device-Code"] = safe_code

    return response

#----------------- Download config file ------------------------------------

import zipfile

@devices_bp.route('/<int:device_id>/download-config', methods=['GET'])
@jwt_required()
def download_device_config(device_id):
    try:
        # Fetch the device
        device = Device.query.get(device_id)
        if not device:
            return jsonify({"error": "Device not found"}), 404

        # Prepare device-specific config
        config = {
            "backend_url": request.host_url.rstrip('/'),
            "device_code": device.device_code,
            "device_token": device.device_token,
            "api_version": "1.0"
        }

        # Server-friendly Python file path
        python_file_path = os.path.join(os.path.dirname(__file__), "..", "PI", "device_app.py")
        python_file_path = os.path.abspath(python_file_path)
        print(f"[INFO] Reading Python file from: {python_file_path}")

        if not os.path.exists(python_file_path):
            return jsonify({"error": f"Python file not found at {python_file_path}"}), 500

        # Read Python file as text
        with open(python_file_path, "r", encoding="utf-8") as f:
            python_code = f.read()

        # Start scripts
        start_sh = "#!/bin/bash\npython3 device_app.py\n"
        start_bat = "@echo off\npython device_app.py\npause\n"

        # Create in-memory ZIP
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            zip_file.writestr("config.json", json.dumps(config, indent=2))
            zip_file.writestr("device_app.py", python_code)
            zip_file.writestr("start.sh", start_sh)
            zip_file.writestr("start.bat", start_bat)

        zip_buffer.seek(0)

        return send_file(
            zip_buffer,
            mimetype='application/zip',
            as_attachment=True,
            download_name=f"device_{device.device_code}_package.zip"
        )

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@devices_bp.route('/register', methods=['POST'])
def register_device():
    data = request.json
    if not data or not data.get('device_code') or not data.get('device_token'):
        return jsonify({"error": "Missing device_code or device_token"}), 400

    device = Device.query.filter_by(
        device_code=data['device_code'],
        device_token=data['device_token']
    ).first()

    if not device:
        return jsonify({"error": "Invalid device credentials"}), 401

    # Update device status
    device.status = 'online'
    device.last_seen = now_ist()  # IST-aware
    device.device_token = None
    db.session.commit()

    return jsonify({
        "message": "Device registered successfully",
        "device_id": device.device_id
    }), 200

@devices_bp.route('/status', methods=['POST'])
def update_device_status():
    data = request.json
    if not data or not data.get('device_code'):
        return jsonify({"error": "Missing device_code"}), 400

    device = Device.query.filter_by(device_code=data['device_code']).first()
    if not device:
        return jsonify({"error": "Device not found"}), 404

    device.last_seen = now_ist()  # IST-aware
    device.status = data.get('status', device.status)
    device.playback_state = data.get('playback_state', device.playback_state)
    
    if 'current_video_id' in data:
        device.current_video_id = data['current_video_id']

    db.session.commit()

    return jsonify({"message": "Status updated successfully"}), 200




@devices_bp.route('/list', methods=['GET'])
@jwt_required()
def list_devices():
    try:
        user_id = int(get_jwt_identity())
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid user ID"}), 422

    user = User.query.filter_by(userId=user_id).first()
    if not user:
        return jsonify({"error": "User not found"}), 404

    devices = Device.query.filter_by(user_id=user.userId).all()
    if not devices:
        return jsonify({"devices": []}), 200

    # Current IST-aware time
    now = now_ist()
    device_list = []

    for d in devices:
        last_seen = d.last_fetch_time
        if last_seen:
            # make sure last_seen is IST-aware
            last_seen = ensure_ist(last_seen)
        
        is_active = (now - last_seen).total_seconds() < 180 if last_seen else False
        new_status = "active" if is_active else "inactive"

        if d.status != new_status:
            d.status = new_status
            db.session.commit()

        current_video = None
        if d.current_video_id:
            video = Video.query.filter_by(video_id=d.current_video_id).first()
            if video:
                current_video = {
                    "video_id": video.video_id,
                    "title": video.title,
                    "description": video.description,
                    "video_link": video.video_link,
                }

        device_list.append({
            "device_id": d.device_id,
            "device_code": d.device_code,
            "status": d.status,
            "last_seen": last_seen.isoformat() if last_seen else None,
            "last_fetch_time": ensure_ist(getattr(d, 'last_fetch_time', None)).isoformat() if getattr(d, 'last_fetch_time', None) else None,
            "next_fetch_time": ensure_ist(getattr(d, 'next_fetch_time', None)).isoformat() if getattr(d, 'next_fetch_time', None) else None,
            "playback_state": d.playback_state,
            "current_video": current_video or None
        })


    return jsonify({"devices": device_list}), 200

#------------------------------ API FOR PI -------------------------------------

from models.models import Schedule, ScheduleVideo, Device, Video
from extensions import db
from datetime import datetime, timedelta, timezone

# IST helpers are imported from utils.timezone at module top

@devices_bp.route("/fetch-schedules", methods=["POST"])
def fetch_schedules():
    data = request.json
    device_token = data.get("device_token")

    device = Device.query.filter_by(device_token=device_token).first()
    if not device:
        return jsonify({"error": "Invalid device token"}), 401

    # Get current IST time (timezone-aware)
    now_aware = now_ist()

    # Update in DB with IST-aware timestamps
    device.status = "active"
    device.last_fetch_time = now_aware
    device.next_fetch_time = now_aware + timedelta(minutes=3)
    db.session.commit()
    # Fetch schedules within the next 12 hours (IST-based)
    next_12h_aware = now_aware + timedelta(hours=12)

    schedules = (
        Schedule.query
        .filter(
            Schedule.device_id == device.device_id,
            Schedule.start_time <= next_12h_aware,
            Schedule.is_active == True
        )
        .order_by(Schedule.start_time.asc())
        .all()
    )

    result = []
    for sch in schedules:
        videos = (
            ScheduleVideo.query
            .filter_by(schedule_group_id=sch.schedule_group_id)
            .join(Video)
            .order_by(ScheduleVideo.order_index.asc())
            .all()
        )

        video_list = []
        for sv in videos:
            video_list.append({
                "video_id": sv.video_id,
                "title": sv.video.title,
                "video_link": sv.video.video_link,
                "download_status": False  
            })

        result.append({
            "schedule_id": sch.schedule_id,
            "schedule_group_id": sch.schedule_group_id,
            "start_time": sch.start_time.isoformat() if sch.start_time else None,
            "end_time": sch.end_time.isoformat() if sch.end_time else None,
            "videos": video_list
        })

    # Return schedules + IST times (formatted)
    return jsonify({
        "schedules": result,
        "fetch_info": {
            "last_fetch_time": now_aware.strftime("%Y-%m-%d %H:%M:%S"),
            "next_fetch_time": (now_aware + timedelta(minutes=3)).strftime("%Y-%m-%d %H:%M:%S")
        }
    })



@devices_bp.route("/update-download-status", methods=["POST"])
def update_download_status():
    data = request.json
    device_token = data.get("device_token")
    video_id = data.get("video_id")
    schedule_group_id = data.get("schedule_group_id")

    device = Device.query.filter_by(device_token=device_token).first()
    if not device:
        return jsonify({"error": "Invalid device token"}), 401

    sv = ScheduleVideo.query.join(Schedule).filter(
        Schedule.device_id == device.device_id,
        ScheduleVideo.video_id == video_id,
        ScheduleVideo.schedule_group_id == schedule_group_id
    ).first()

    if sv:
        sv.download_status = True
        db.session.commit()

    return jsonify({"message": "Download status updated"})

@devices_bp.route("/update-playback", methods=["POST"])
def update_playback():
    try:
        data = request.get_json(force=True)
    except Exception:
        return jsonify({"error": "Invalid JSON"}), 400

    token = data.get("device_token")
    video_id = data.get("video_id")
    playback_state = data.get("playback_state", "playing")

    if not token:
        return jsonify({"error": "Missing device_token"}), 400

    device = Device.query.filter_by(device_token=token).first()
    if not device:
        return jsonify({"error": "Device not found"}), 404

    valid_states = {"playing", "paused", "stopped"}
    if playback_state not in valid_states:
        return jsonify({"error": f"Invalid playback_state '{playback_state}'"}), 400

    device.last_seen = now_ist()  # IST-aware
    device.status = "active" if playback_state == "playing" else "idle"
    device.playback_state = playback_state
    device.current_video_id = video_id

    db.session.commit()

    return jsonify({
        "message": "Playback state updated",
        "device_code": device.device_code,
        "last_seen": device.last_seen.isoformat()
    }), 200
