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
    # Get the raw token identity
    user_id = get_jwt_identity()
    print("JWT identity:", user_id, type(user_id))
    print("Request headers:", request.headers)
    
    # Get the full JWT claims
    jwt_claims = get_jwt()
    print("JWT claims:", jwt_claims)

    # Validate user_id
    try:
        # Always convert to string first, then to int
        user_id_str = str(user_id)
        user_id = int(user_id_str)
    except (TypeError, ValueError):
        return jsonify({
            "error": f"Invalid user ID in token: {user_id}",
            "type": str(type(user_id))
        }), 422

    from models.models import User 
    user = User.query.filter_by(userId=user_id).first()
    if not user:
        return jsonify({"error": "User not found"}), 404

    devices = Device.query.filter_by(user_id=user.userId).all()
    device_list = [{
        'device_id': d.device_id,
        'device_code': d.device_code,
        'status': d.status,
        'last_seen': d.last_seen.isoformat() if d.last_seen else None,
        'playback_state': d.playback_state,
        'current_video_id': d.current_video_id
    } for d in devices]

    print(device_list)

    return jsonify({"devices": device_list}), 200



#------------------------------ API FOR PI -----------------------------------------------

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
        # optional: add a download_status field in ScheduleVideo or Video
        sv.download_status = True  # if using a dedicated column
        db.session.commit()

    return jsonify({"message": "Download status updated"})


#---------------------------------------------------------------------------------------------------