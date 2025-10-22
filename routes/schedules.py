# schedules.py
from flask import Blueprint, request, jsonify
from datetime import datetime, timedelta, timezone
from flask_jwt_extended import jwt_required, get_jwt_identity
from models.models import Schedule, Video, Device, ScheduleVideo
from extensions import db
import random

# Define IST timezone
IST = timezone(timedelta(hours=5, minutes=30))

schedules_bp = Blueprint('schedules', __name__)

@schedules_bp.route('/create', methods=['POST'])
@jwt_required()
def create_schedule_api():
    user_id = get_jwt_identity()
    data = request.json

    # Fetch video and device
    video = Video.query.get_or_404(data['video_id'])
    device = Device.query.get_or_404(data['device_id'])

    # Debug logs
    print("JWT user_id:", user_id)
    print("Video user_id:", video.user_id, "Video ID:", video.video_id)
    print("Device user_id:", device.user_id, "Device ID:", device.device_id)

    # Ownership checks
    if int(video.user_id) != int(user_id):
        print("Blocked: video does not belong to user")
        return jsonify({"msg": "not allowed"}), 403

    if int(device.user_id) != int(user_id):
        print("Blocked: device does not belong to user")
        return jsonify({"msg": "not allowed"}), 403

    # Convert incoming ISO timestamps to IST-aware datetime
    start_time = datetime.fromisoformat(data['start_time']).astimezone(IST)
    end_time = datetime.fromisoformat(data['end_time']).astimezone(IST) if data.get('end_time') else None

    s = Schedule(
        video_id=video.video_id,
        device_id=device.device_id,
        start_time=start_time,
        end_time=end_time,
        repeat=data.get('repeat', False),
        days_of_week=data.get('days_of_week'),
        play_mode=data.get('play_mode', 'loop'),
        is_active=True
    )

    db.session.add(s)
    db.session.commit()
    return jsonify({"schedule_id": s.schedule_id}), 201


#---------------API FOR MULTI-VIDEO SCHEDULING ----------------------

@schedules_bp.route('/create-multiple', methods=['POST'])
@jwt_required()
def create_multiple_schedules():
    user_id = int(get_jwt_identity())
    data = request.json

    device_ids = data.get("deviceIds", [])
    video_ids = data.get("videoIds", [])
    start_time_str = data.get("startTime")
    end_time_str = data.get("endTime")
    repeat = data.get("repeat", False)
    play_mode = data.get("playMode", "loop")

    if not device_ids or not video_ids or not start_time_str:
        return jsonify({"msg": "Devices, Videos, and Start time are required"}), 400

    try:
        # Convert to IST-aware datetime
        start_time = datetime.fromisoformat(start_time_str).astimezone(IST)
        end_time = datetime.fromisoformat(end_time_str).astimezone(IST) if end_time_str else None

        created_schedules = []

        # Generate one group ID for this batch (IST timestamp in ms)
        schedule_group_id = int(datetime.now(IST).timestamp() * 1000)

        # Insert Schedule rows for each device
        for device_id in device_ids:
            device = Device.query.get(device_id)
            if not device:
                return jsonify({"msg": f"Device {device_id} not found"}), 404
            if device.user_id != user_id:
                return jsonify({"msg": f"Device {device_id} not allowed"}), 403

            schedule = Schedule(
                device_id=device.device_id,
                schedule_group_id=schedule_group_id,
                start_time=start_time,
                end_time=end_time,
                repeat=repeat,
                play_mode=play_mode,
                is_active=True
            )
            db.session.add(schedule)
            created_schedules.append(schedule)

        # Insert ScheduleVideo rows (shared for the group)
        for idx, video_id in enumerate(video_ids):
            video = Video.query.get(video_id)
            if not video:
                return jsonify({"msg": f"Video {video_id} not found"}), 404
            if video.user_id != user_id:
                return jsonify({"msg": f"Video {video_id} not allowed"}), 403

            schedule_video = ScheduleVideo(
                schedule_group_id=schedule_group_id,
                video_id=video.video_id,
                order_index=idx
            )
            db.session.add(schedule_video)

        db.session.commit()

        return jsonify({
            "msg": "Schedules created successfully",
            "schedule_group_id": schedule_group_id,
            "schedule_ids": [s.schedule_id for s in created_schedules]
        }), 201

    except Exception as e:
        db.session.rollback()
        return jsonify({"msg": f"Failed to create schedules: {str(e)}"}), 500
