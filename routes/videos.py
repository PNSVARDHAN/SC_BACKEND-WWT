# videos.py
import os
from flask import Blueprint, request, jsonify, Response
from flask_jwt_extended import jwt_required, get_jwt_identity
from models.models import Video
from extensions import db
from datetime import datetime
import io
from flask import send_file

videos_bp = Blueprint('videos', __name__)

ALLOWED_EXT = {'mp4', 'mov', 'mkv', 'avi'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.',1)[1].lower() in ALLOWED_EXT

#----------------------API FOR VIDEO UPLOAD---------------------------------
from werkzeug.utils import secure_filename

# Create uploads folder if it doesn't exist
UPLOAD_FOLDER = "/tmp/uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


@videos_bp.route("/upload", methods=["POST"])
@jwt_required()
def upload_video():
    user_id = get_jwt_identity()
    file = request.files.get("file")
    title = request.form.get("title")
    description = request.form.get("description")
    # Get is_default from form, default to False
    is_default = request.form.get("is_default", "false").lower() == "true"

    if not file:
        return jsonify({"msg": "No file provided"}), 400
    if not title:
        title = file.filename

    duration = request.form.get("duration")
    if duration:
        duration = int(duration)
    else:
        duration = None  


    try:
        # Save file to temp folder
        filename = secure_filename(file.filename)
        save_path = os.path.join(UPLOAD_FOLDER, filename)
        file.save(save_path)

        # Generate a "link"
        video_link = f"/videos/{filename}"   # Example: API endpoint to fetch video

        # Save metadata in DB
        video = Video(
            title=title,
            description=description,
            video_link=video_link,
            uploaded_at=datetime.utcnow(),
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


#-----------------------____________________________________---------------------------------


@videos_bp.route('/<int:video_id>/download', methods=['GET'])
@jwt_required()
def download_video(video_id):
    vid = Video.query.get_or_404(video_id)
    user_id = get_jwt_identity()
    if vid.user_id != user_id:
        return jsonify({"msg":"forbidden"}), 403

    return send_file(
        io.BytesIO(vid.video_data),
        mimetype="video/mp4",  # adjust if needed
        download_name=f"{vid.title}",
        as_attachment=True
    )

import os
from flask import Response, send_file

@videos_bp.route("/my-videos", methods=["GET"])
@jwt_required()
def get_user_videos():
    """Return metadata + stream URLs for user videos"""
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
            "uploadedAt": v.uploaded_at.strftime("%Y-%m-%d %H:%M:%S"),
            "videoUrl": f"/videos/{v.video_id}/stream"
        }
        for v in videos
    ]
    return jsonify(result), 200


@videos_bp.route("/<int:video_id>/stream", methods=["GET"])
def stream_video(video_id):
    """Stream video from file path stored in DB"""
    video = Video.query.get_or_404(video_id)

    if not video.video_link:
        return jsonify({"msg": "No video file found"}), 404

    file_path = os.path.join("/tmp/uploads", os.path.basename(video.video_link))
    if not os.path.exists(file_path):
        return jsonify({"msg": "File not found on server"}), 404

    # Use send_file with partial content support for streaming
    range_header = request.headers.get("Range", None)
    if not range_header:
        return send_file(file_path, mimetype="video/mp4")

    # Handle byte ranges for streaming
    size = os.path.getsize(file_path)
    byte1, byte2 = 0, None
    m = range_header.replace("bytes=", "").split("-")
    if m[0]:
        byte1 = int(m[0])
    if len(m) > 1 and m[1]:
        byte2 = int(m[1])

    byte2 = byte2 if byte2 is not None else size - 1
    length = byte2 - byte1 + 1

    with open(file_path, "rb") as f:
        f.seek(byte1)
        data = f.read(length)

    resp = Response(data, status=206, mimetype="video/mp4")
    resp.headers.add("Content-Range", f"bytes {byte1}-{byte2}/{size}")
    resp.headers.add("Accept-Ranges", "bytes")
    resp.headers.add("Content-Length", str(length))

    # Add CORS headers
    resp.headers['Access-Control-Allow-Origin'] = '*'
    resp.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
    resp.headers['Access-Control-Allow-Headers'] = 'Authorization'

    return resp


from flask import send_from_directory

@videos_bp.route("/<path:filename>", methods=["GET"])
def serve_video(filename):
    """Serve video files from UPLOAD_FOLDER"""
    return send_from_directory(UPLOAD_FOLDER, filename, mimetype="video/mp4")



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

    # Reset all to false
    Video.query.update({Video.is_default: False})
    video.is_default = True
    db.session.commit()

    return jsonify({"message": f"{video.title} set as default"})


