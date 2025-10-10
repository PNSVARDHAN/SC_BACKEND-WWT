import uuid, json
from datetime import datetime
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

#_________________downloading the config file ------------------------------------


@devices_bp.route('/<int:device_id>/download-config', methods=['GET'])
@jwt_required()
def download_device_config(device_id):
    # Fetch the device
    device = Device.query.get(device_id)
    if not device:
        return jsonify({"error": "Device not found"}), 404

    # Prepare config
    config = {
        "backend_url": request.host_url.rstrip('/'),
        "device_code": device.device_code,
        "device_token": device.device_token,
        "api_version": "1.0"
    }

    # Create config file in memory
    config_file = io.StringIO()
    json.dump(config, config_file, indent=2)
    config_file.seek(0)

    # Send as downloadable file
    return send_file(
        io.BytesIO(config_file.getvalue().encode()),
        mimetype='application/json',
        as_attachment=True,
        download_name=f'device_{device.device_code}_config.json'
    )

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
    device.last_seen = datetime.utcnow()
    device.device_token = None  # Invalidate one-time token
    db.session.commit()

    return jsonify({
        "message": "Device registered successfully",
        "device_id": device.device_id
    }), 200


@devices_bp.route('/status', methods=['POST'])
def update_device_status():
    """Update device status and playback information"""
    data = request.json
    if not data or not data.get('device_code'):
        return jsonify({"error": "Missing device_code"}), 400

    device = Device.query.filter_by(device_code=data['device_code']).first()
    if not device:
        return jsonify({"error": "Device not found"}), 404

    # Update device status
    device.last_seen = datetime.utcnow()
    device.status = data.get('status', device.status)
    device.playback_state = data.get('playback_state', device.playback_state)
    
    if 'current_video_id' in data:
        device.current_video_id = data['current_video_id']

    db.session.commit()

    return jsonify({"message": "Status updated successfully"}), 200

@devices_bp.route('/list', methods=['GET'])
@jwt_required()
def list_devices():
    """
    Return all devices belonging to the logged-in user,
    including playback status and currently playing video info.
    """
    user_id = get_jwt_identity()
    try:
        user_id = int(str(user_id))
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid user ID"}), 422

    from models.models import User, Device, Video
    user = User.query.filter_by(userId=user_id).first()
    if not user:
        return jsonify({"error": "User not found"}), 404

    devices = Device.query.filter_by(user_id=user.userId).all()
    if not devices:
        return jsonify({"devices": []}), 200

    device_list = []
    now = datetime.utcnow()

    for d in devices:
        # Determine "active" or "inactive" dynamically (last seen < 20 sec)
        is_active = (
            d.last_seen and (now - d.last_seen).total_seconds() < 20
        )
        status = "active" if is_active else "inactive"

        # Fetch video info if available
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
            "status": status,
            "last_seen": d.last_seen.isoformat() if d.last_seen else None,
            "playback_state": d.playback_state,
            "current_video": current_video or None
        })

    return jsonify({"devices": device_list}), 200



#------------------------------ API FOR PI -------------------------------------

from flask import Blueprint, request, jsonify
from datetime import datetime, timedelta
from models.models import Device, Schedule, Video, db , ScheduleVideo

@devices_bp.route("/fetch-schedules", methods=["POST"])
def fetch_schedules():
    data = request.json
    device_token = data.get("device_token")

    device = Device.query.filter_by(device_token=device_token).first()
    if not device:
        return jsonify({"error": "Invalid device token"}), 401

    now = datetime.utcnow()
    next_12h = now + timedelta(hours=12)

    schedules = (
        Schedule.query
        .filter(
            Schedule.device_id == device.device_id,
            Schedule.start_time <= next_12h,
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
            "start_time": sch.start_time.isoformat(),
            "end_time": sch.end_time.isoformat() if sch.end_time else None,
            "videos": video_list
        })

    return jsonify({"schedules": result})


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
    """
    Called by the Pi to report current video and playback status.
    """
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

    # Optional: validate playback_state
    valid_states = {"playing", "paused", "stopped"}
    if playback_state not in valid_states:
        return jsonify({"error": f"Invalid playback_state '{playback_state}'"}), 400

    # Update playback info
    device.last_seen = datetime.utcnow()
    device.status = "active" if playback_state == "playing" else "idle"
    device.playback_state = playback_state
    device.current_video_id = video_id

    db.session.commit()

    return jsonify({
        "message": "Playback state updated",
        "device_code": device.device_code,
        "last_seen": device.last_seen.isoformat()
    }), 200



#-----------------------------------------------------------------