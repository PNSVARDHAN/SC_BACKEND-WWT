from datetime import datetime
from utils.timezone import now_ist
from extensions import db
from sqlalchemy.dialects.mysql import LONGBLOB

class User(db.Model):
    __tablename__ = 'users'

    userId = db.Column(db.Integer, primary_key=True, autoincrement=True)
    username = db.Column(db.String(70), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=True)
    google_id = db.Column(db.String(200), unique=True, nullable=True)
    created_at = db.Column(db.DateTime, default=now_ist)

    devices = db.relationship("Device", backref="owner", lazy=True, cascade="all, delete")
    videos = db.relationship("Video", backref="user", lazy=True, cascade="all, delete")

    def __repr__(self):
        return f"<User {self.username}>"


class Device(db.Model):
    __tablename__ = 'devices'

    device_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    device_code = db.Column(db.String(100), unique=True, nullable=False)
    device_token = db.Column(db.String(100), unique=True, nullable=True)  # One-time registration 
    status = db.Column(db.String(20), default="inactive")  
    last_seen = db.Column(db.DateTime, nullable=True)
    current_video_id = db.Column(db.Integer, db.ForeignKey('videos.video_id'), nullable=True)
    playback_state = db.Column(db.String(20), default="stopped")  # playing, paused, stopped
    created_at = db.Column(db.DateTime, default=now_ist)
    updated_at = db.Column(db.DateTime, default=now_ist, onupdate=now_ist)
    last_fetch_time = db.Column(db.DateTime, nullable=True)
    next_fetch_time = db.Column(db.DateTime, nullable=True)

    user_id = db.Column(db.Integer, db.ForeignKey('users.userId'), nullable=False)

    schedules = db.relationship("Schedule", backref="device", lazy=True, cascade="all, delete")

    def __repr__(self):
        return f"<Device {self.device_code}>"


class Video(db.Model):
    __tablename__ = 'videos'

    video_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    title = db.Column(db.String(150), nullable=False)
    description = db.Column(db.Text, nullable=True)
    video_link = db.Column(db.String(200), nullable=True)
    duration = db.Column(db.Integer, nullable=True)
    uploaded_at = db.Column(db.DateTime, default=now_ist)
    is_default = db.Column(db.Boolean, default=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.userId'), nullable=False)
    def __repr__(self):
        return f"<Video {self.title}>"

class Schedule(db.Model):
    __tablename__ = 'schedules'

    schedule_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    device_id = db.Column(db.Integer, db.ForeignKey("devices.device_id"), nullable=False)
    schedule_group_id = db.Column(db.BigInteger, index=True, nullable=False)  # Group across devices
    start_time = db.Column(db.DateTime, nullable=False)
    end_time = db.Column(db.DateTime, nullable=True)
    repeat = db.Column(db.Boolean, default=False)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=now_ist)
    play_mode = db.Column(db.String(20), default="loop")
    download_status = db.Column(db.Boolean, default=False)

    def __repr__(self):
        return f"<Schedule {self.schedule_id} (Group {self.schedule_group_id}) - Device {self.device_id}>"
    


class ScheduleVideo(db.Model):
    __tablename__ = "schedule_videos"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    schedule_group_id = db.Column(db.BigInteger, nullable=False, index=True)   # Link to group
    video_id = db.Column(db.Integer, db.ForeignKey("videos.video_id"), nullable=False)
    order_index = db.Column(db.Integer, nullable=False)  

    video = db.relationship("Video", backref="schedule_entries", lazy=True)

    def __repr__(self):
        return f"<ScheduleVideo {self.video_id} in Group {self.schedule_group_id} at position {self.order_index}>"




class New_Devices(db.Model):
    __tablename__ = 'newdevices'

    device_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    device_name = db.Column(db.String(100), nullable=False)
    device_code = db.Column(db.String(100), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=now_ist, nullable=False)

    def __repr__(self):
        return f"<Device {self.device_name} ({self.device_code})>"

