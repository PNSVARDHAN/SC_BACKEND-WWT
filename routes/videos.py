# videos.py
import os
from flask import Blueprint, request, jsonify, Response, redirect
from flask_jwt_extended import jwt_required, get_jwt_identity
from models.models import Video, Schedule, ScheduleVideo, Device
from datetime import datetime, timedelta, timezone
from utils.timezone import IST, now_ist, ensure_ist
from extensions import db
import io
from flask import send_file
import boto3
from dotenv import load_dotenv
from sqlalchemy.orm import joinedload
from werkzeug.utils import secure_filename

load_dotenv()
videos_bp = Blueprint('videos', __name__)

ALLOWED_EXT = {'mp4', 'mov', 'mkv', 'avi'}

# IST timezone helpers provided by utils.timezone

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.',1)[1].lower() in ALLOWED_EXT

# Cloudflare R2 config
R2_ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID")
R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID")
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY")
R2_BUCKET_NAME = os.getenv("R2_BUCKET_NAME")
R2_ENDPOINT_URL = f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com" if R2_ACCOUNT_ID else None

if R2_ENDPOINT_URL and R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY:
    s3_client = boto3.client(
        "s3",
        endpoint_url=R2_ENDPOINT_URL,
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
    )
else:
    s3_client = None

PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL")

def build_public_url(object_key: str) -> str:
    """Return a public URL for a given object key using env configuration.
    Falls back to account API endpoint if PUBLIC_BASE_URL isn't configured.
    """
    if PUBLIC_BASE_URL:
        return f"{PUBLIC_BASE_URL}/{object_key}"
    if R2_ENDPOINT_URL and R2_BUCKET_NAME:
        return f"{R2_ENDPOINT_URL}/{R2_BUCKET_NAME}/{object_key}"
    return object_key

def extract_object_key(file_url: str) -> str:
    """Best-effort to get the R2 object key from a stored URL regardless of domain style."""
    try:
        if PUBLIC_BASE_URL and file_url.startswith(PUBLIC_BASE_URL + "/"):
            return file_url[len(PUBLIC_BASE_URL) + 1:]
        if R2_ENDPOINT_URL:
            api_prefix = f"{R2_ENDPOINT_URL}/"
            if file_url.startswith(api_prefix):
                suffix = file_url[len(api_prefix):]
                if R2_BUCKET_NAME and suffix.startswith(f"{R2_BUCKET_NAME}/"):
                    return suffix[len(R2_BUCKET_NAME) + 1:]
                return suffix
        parts = file_url.split("/")
        if R2_BUCKET_NAME in parts:
            idx = parts.index(R2_BUCKET_NAME)
            return "/".join(parts[idx + 1:])
        return "/".join(parts[3:]) if len(parts) > 3 else parts[-1]
    except Exception:
        return file_url

# ---------------- Upload Video ----------------
@videos_bp.route("/upload", methods=["POST"])
@jwt_required()
def upload_video():
    try:
        user_id = get_jwt_identity()
        file = request.files.get("file")
        title = request.form.get("title")
        description = request.form.get("description")
        is_default = request.form.get("is_default", "false").lower() == "true"
        duration = request.form.get("duration")

        if not file:
            return jsonify({"msg": "No file provided"}), 400

        filename = secure_filename(file.filename)
        if not title:
            title = filename

        duration = int(duration) if duration else None
        object_key = f"videos/{user_id}/{filename}"

        if s3_client is None:
            return jsonify({"msg": "Cloud storage not configured"}), 500

        s3_client.upload_fileobj(
            file,
            R2_BUCKET_NAME,
            object_key,
            ExtraArgs={"ContentType": file.content_type}
        )

        video_link = build_public_url(object_key)

        video = Video(
            title=title,
            description=description,
            video_link=video_link,
            uploaded_at=now_ist(),
            user_id=user_id,
            is_default=is_default,
            duration=duration
        )

        db.session.add(video)
        db.session.commit()

        return jsonify({
            "msg": "Video uploaded successfully",
            "video_id": video.video_id,
            "title": video.title,
            "video_link": video.video_link,
            "is_default": video.is_default
        }), 201

    except Exception as e:
        db.session.rollback()
        return jsonify({"msg": f"Upload failed: {str(e)}"}), 500

# ---------------- Get User Videos ----------------
@videos_bp.route("/my-videos", methods=["GET"])
@jwt_required()
def get_user_videos():
    user_id = get_jwt_identity()
    videos = Video.query.filter_by(user_id=user_id).all()

    if not videos:
        return jsonify([]), 200

    result = [
        {
            "videoId": v.video_id,
            "title": v.title,
            "description": v.description,
            "duration": v.duration,
            "uploadedAt": ensure_ist(v.uploaded_at).strftime("%Y-%m-%d %H:%M:%S"),
            "videoUrl": v.video_link
        }
        for v in videos
    ]
    return jsonify(result), 200

# ---------------- Stream Video ----------------
@videos_bp.route("/<int:video_id>/stream", methods=["GET"])
def stream_video(video_id):
    video = Video.query.get_or_404(video_id)
    if not video.video_link:
        return jsonify({"msg": "No video link found"}), 404
    return redirect(video.video_link, code=302)

# ---------------- Download Video ----------------
@videos_bp.route("/<int:video_id>/download", methods=["GET"])
@jwt_required()
def download_video(video_id):
    video = Video.query.get_or_404(video_id)
    user_id = get_jwt_identity()

    if video.user_id != user_id:
        return jsonify({"msg": "Forbidden"}), 403

    try:
        object_key = extract_object_key(video.video_link)
        if s3_client is None:
            return jsonify({"msg": "Cloud storage not configured"}), 500
        presigned_url = s3_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": R2_BUCKET_NAME, "Key": object_key},
            ExpiresIn=300
        )
        return jsonify({"downloadUrl": presigned_url}), 200
    except Exception as e:
        return jsonify({"msg": f"Failed to generate download URL: {str(e)}"}), 500

# ---------------- Default Video ----------------
@videos_bp.route("/default-video", methods=["GET"])
def get_default_video():
    video = Video.query.filter_by(is_default=True).first()
    if not video:
        return jsonify({"error": "No default video found"}), 404
    return jsonify({
        "video_id": video.video_id,
        "title": video.title,
        "video_link": video.video_link
    })

@videos_bp.route("/set-default/<int:video_id>", methods=["POST"])
def set_default_video(video_id):
    video = Video.query.get(video_id)
    if not video:
        return jsonify({"error": "Video not found"}), 404

    Video.query.update({Video.is_default: False})
    video.is_default = True
    db.session.commit()

    return jsonify({"message": f"{video.title} set as default"})

# ---------------- Next Scheduled Videos (IST) ----------------
@videos_bp.route("/my-next-videos", methods=["GET"])
@jwt_required()
def get_user_next_videos():
    user_id = get_jwt_identity()
    now = now_ist()
    print("Current IST time:", now)

    upcoming_schedules = (
        db.session.query(Schedule)
        .join(Device, Device.device_id == Schedule.device_id)
        .filter(Device.user_id == user_id, Schedule.is_active == True)
        .order_by(Schedule.start_time.asc())
        .all()
    )

    result = []
    for schedule in upcoming_schedules:
        current_time = ensure_ist(schedule.start_time)

        schedule_videos = (
            db.session.query(ScheduleVideo)
            .filter(ScheduleVideo.schedule_group_id == schedule.schedule_group_id)
            .order_by(ScheduleVideo.order_index.asc())
            .options(joinedload(ScheduleVideo.video))
            .all()
        )

        for sv in schedule_videos:
            video_duration = sv.video.duration or 0
            video_end_time = current_time + timedelta(seconds=video_duration)

            if video_end_time < now:
                current_time = video_end_time
                continue

            result.append({
                "videoId": sv.video.video_id,
                "title": sv.video.title,
                "description": sv.video.description,
                "duration": sv.video.duration,
                "startTime": current_time.strftime("%Y-%m-%d %H:%M:%S"),
                "endTime": video_end_time.strftime("%Y-%m-%d %H:%M:%S"),
                "deviceId": schedule.device_id,
                "scheduleGroupId": schedule.schedule_group_id,
                "videoUrl": sv.video.video_link
            })

            current_time = video_end_time

    return jsonify(result), 200

# ---------------- Delete Video ----------------
@videos_bp.route("/delete/<int:video_id>", methods=["DELETE"])
@jwt_required()
def delete_video(video_id):
    user_id = get_jwt_identity()
    try:
        video = Video.query.filter_by(video_id=video_id, user_id=user_id).first()
        if not video:
            return jsonify({"msg": "Video not found"}), 404

        if video.video_link:
            try:
                if s3_client is None:
                    raise RuntimeError("Cloud storage not configured")
                video_key = extract_object_key(video.video_link)
                s3_client.delete_object(Bucket=R2_BUCKET_NAME, Key=video_key)
            except Exception as e:
                print(f"[WARN] Failed to delete from R2: {e}")

        devices_using_video = Device.query.filter_by(current_video_id=video_id).all()
        for d in devices_using_video:
            d.current_video_id = None

        ScheduleVideo.query.filter_by(video_id=video_id).delete()
        db.session.delete(video)
        db.session.commit()

        return jsonify({"msg": "Video deleted successfully"}), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({"msg": f"Error deleting video: {str(e)}"}), 500

    
