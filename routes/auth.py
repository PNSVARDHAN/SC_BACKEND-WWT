import os
from flask import Blueprint, request, jsonify
from extensions import db
from flask_jwt_extended import create_access_token
from models.models import User
import google.auth.transport.requests
import google.oauth2.id_token
from datetime import datetime, timedelta, timezone
from utils.timezone import IST, now_ist, ensure_ist
import random
from werkzeug.security import generate_password_hash, check_password_hash
import smtplib
from email.mime.text import MIMEText

# centralized IST helpers imported above

# ---------------------------- Blueprint ----------------------------
auth_bp = Blueprint("auth", __name__)

# ---------------------------- OTP STORAGE ----------------------------
otp_storage = {}
OTP_EXPIRY_MINUTES = 5

SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_SENDER = os.getenv("SMTP_EMAIL")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")

# ============================= Helper: Send Email =============================
def send_email(recipient, subject, body):
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = SMTP_SENDER
    msg["To"] = recipient

    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_SENDER, SMTP_PASSWORD)
        server.send_message(msg)

# ============================= Helper: OTP =============================
def generate_and_store_otp(email):
    otp = random.randint(100000, 999999)
    otp_storage[email] = {
        "otp": otp,
        "expires": now_ist() + timedelta(minutes=OTP_EXPIRY_MINUTES)
    }
    print(f"[{now_ist()}] OTP {otp} generated for {email}")
    return otp

def validate_otp(email, otp):
    if email not in otp_storage:
        return "OTP not requested"
    record = otp_storage[email]
    if now_ist() > record["expires"]:
        otp_storage.pop(email, None)
        return "OTP expired"
    if int(otp) != record["otp"]:
        return "Invalid OTP"
    otp_storage.pop(email, None)
    return None  # valid

# ============================= Login =============================
@auth_bp.route("/login", methods=["POST", "OPTIONS"])
def login():
    if request.method == "OPTIONS":
        return '', 204

    data = request.json
    email = data.get("email")
    password = data.get("password")

    user = User.query.filter_by(email=email).first()
    if not user:
        return jsonify({"error": "Invalid credentials"}), 401

    if not user.password:
        return jsonify({"error": "No password set. Use Forgot Password to set a password."}), 400

    if not check_password_hash(user.password, password):
        return jsonify({"error": "Invalid credentials"}), 401

    token = create_access_token(identity=str(user.userId))
    return jsonify({
        "message": "Login successful", 
        "token": token,
        "user": {"id": user.userId}
    })

# ============================= Google Login =============================
@auth_bp.route("/google-login", methods=["POST", "OPTIONS"])
def google_login():
    if request.method == "OPTIONS":
        return '', 204

    token = request.json.get("token")
    try:
        id_info = google.oauth2.id_token.verify_oauth2_token(
            token, google.auth.transport.requests.Request()
        )
        email = id_info.get("email")
        google_id = id_info.get("sub")
        name = id_info.get("name", "Google User")

        user = User.query.filter_by(email=email).first()
        if not user:
            user = User(
                username=name,
                email=email,
                google_id=google_id,
                password=None,
                created_at=now_ist()
            )
            db.session.add(user)
            db.session.commit()

        access_token = create_access_token(identity=str(user.userId))
        return jsonify({"message": "Google login successful", "token": access_token})

    except Exception as e:
        return jsonify({"error": "Invalid Google token", "details": str(e)}), 400

# ============================= Get User =============================
@auth_bp.route("/users/<int:user_id>", methods=["GET"])
def get_user(user_id):
    user = User.query.get_or_404(user_id)
    return jsonify({
        "userId": user.userId,
        "username": user.username,
        "email": user.email,
        "created_at": user.created_at
    })

# ============================= Signup =============================
@auth_bp.route("/signup", methods=["POST", "OPTIONS"])
def signup():
    if request.method == "OPTIONS":
        return '', 204

    data = request.get_json()
    email = data.get("email")
    username = data.get("username")
    password = data.get("password")

    if not all([email, username, password]):
        return jsonify({"error": "All fields required"}), 400

    if User.query.filter_by(email=email).first():
        return jsonify({"error": "User already exists"}), 400

    hashed_pw = generate_password_hash(password)
    otp = generate_and_store_otp(email)

    try:
        subject = "Verify Your Account"
        body = (
            f"Hello {username},\n\n"
            f"Your OTP for account verification is: {otp}\n"
            f"It is valid for {OTP_EXPIRY_MINUTES} minutes."
        )
        send_email(email, subject, body)
        return jsonify({
            "message": "OTP sent to your email for verification",
            "temp_user": {"email": email, "username": username, "password": hashed_pw}
        }), 200
    except Exception as e:
        print("SMTP Error:", e)
        return jsonify({"error": "Failed to send OTP"}), 500

# ============================= Verify Signup OTP =============================
@auth_bp.route("/verify-signup-otp", methods=["POST"])
def verify_signup_otp():
    data = request.get_json()
    email = data.get("email")
    otp = data.get("otp")
    username = data.get("username")
    password = data.get("password")

    if not all([email, otp, username, password]):
        return jsonify({"error": "Missing data"}), 400

    error = validate_otp(email, otp)
    if error:
        return jsonify({"error": error}), 400

    new_user = User(
        username=username,
        email=email,
        password=generate_password_hash(password),
        created_at=now_ist()
    )
    db.session.add(new_user)
    db.session.commit()

    token = create_access_token(identity=str(new_user.userId))
    return jsonify({
        "message": "Signup successful",
        "token": token,
        "user": {"id": new_user.userId, "email": email}
    }), 200

# ============================= Forgot Password =============================
@auth_bp.route("/forgot-password", methods=["POST"])
def forgot_password():
    data = request.get_json()
    email = data.get("email")

    if not email:
        return jsonify({"error": "Email required"}), 400

    user = User.query.filter_by(email=email).first()
    if not user:
        return jsonify({"error": "User not found"}), 404

    otp = generate_and_store_otp(email)
    try:
        subject = "Password Reset OTP"
        body = (
            f"Hello {user.username},\n\n"
            f"Your OTP for password reset is: {otp}\n"
            f"It is valid for {OTP_EXPIRY_MINUTES} minutes."
        )
        send_email(email, subject, body)
        return jsonify({"message": "OTP sent to your email"}), 200
    except Exception as e:
        print("SMTP Error:", e)
        return jsonify({"error": "Failed to send OTP"}), 500

# ============================= Verify OTP =============================
@auth_bp.route("/verify-otp", methods=["POST"])
def verify_otp():
    data = request.get_json()
    email = data.get("email")
    otp = data.get("otp")

    if not email or not otp:
        return jsonify({"error": "Email and OTP required"}), 400

    error = validate_otp(email, otp)
    if error:
        return jsonify({"error": error}), 400

    return jsonify({"message": "OTP verified"}), 200

# ============================= Reset Password =============================
@auth_bp.route("/reset-password", methods=["POST"])
def reset_password():
    data = request.get_json()
    email = data.get("email")
    new_password = data.get("password")

    if not all([email, new_password]):
        return jsonify({"error": "Email and new password required"}), 400

    user = User.query.filter_by(email=email).first()
    if not user:
        return jsonify({"error": "User not found"}), 404

    user.password = generate_password_hash(new_password)
    db.session.commit()

    otp_storage.pop(email, None)
    return jsonify({"message": "Password reset successful"}), 200
