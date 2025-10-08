import os
from flask import Blueprint, request, jsonify
from extensions import db
from flask_jwt_extended import create_access_token
from models.models import User
import google.auth.transport.requests
import google.oauth2.id_token
from datetime import datetime, timedelta
import random
from werkzeug.security import generate_password_hash, check_password_hash
import smtplib
from email.mime.text import MIMEText

auth_bp = Blueprint("auth", __name__)

otp_storage = {}


# ------------------ Signup ------------------
@auth_bp.route("/signup", methods=["POST", "OPTIONS"])
def signup():
    if request.method == "OPTIONS":
        return '', 204

    data = request.json
    email = data.get("email")
    username = data.get("username")
    password = data.get("password")

    if not email or not username or not password:
        return jsonify({"error": "All fields required"}), 400

    existing_user = User.query.filter_by(email=email).first()
    if existing_user:
        return jsonify({"error": "User already exists"}), 400

    hashed_pw = generate_password_hash(password)
    new_user = User(username=username, email=email, password=hashed_pw)
    db.session.add(new_user)
    db.session.commit()

    token = create_access_token(identity=str(new_user.userId))
    return jsonify({
    "message": "Signup successful",
    "token": token,
    "user": {
        "id": new_user.userId,
    }
})

# ------------------ Login ------------------
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

    # Google-login user without password
    if not user.password:
        return jsonify({
            "error": "No password set. Use Forgot Password to set a password."
        }), 400

    if not check_password_hash(user.password, password):
        return jsonify({"error": "Invalid credentials"}), 401

    # Create token with additional claims
    user_id_str = str(user.userId)
    print(f"Creating token with user_id: {user_id_str} (type: {type(user_id_str)})")
    token = create_access_token(identity=str(user.userId))
    print(f"Created token for user {user.userId} (email: {user.email})")
    return jsonify({
        "message": "Login successful", 
        "token": token,
        "user": {
            "id": user.userId,
        }
    })


# ------------------ Google Login ------------------
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
            # New Google user, no password
            user = User(username=name, email=email, google_id=google_id, password=None)
            db.session.add(user)
            db.session.commit()

        access_token = create_access_token(identity=str(user.userId))
        return jsonify({"message": "Google login successful", "token": access_token})

    except Exception as e:
        return jsonify({"error": "Invalid Google token", "details": str(e)}), 400


# ------------------ Forgot Password ------------------
@auth_bp.route("/forgot-password", methods=["POST"])
def forgot_password():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    email = data.get("email")
    user = User.query.filter_by(email=email).first()
    if not user:
        return jsonify({"error": "User not found"}), 404

    otp = random.randint(100000, 999999)
    otp_storage[email] = {"otp": otp, "expires": datetime.utcnow() + timedelta(minutes=5)}

    # --- Send OTP via SMTP ---
    try:
        subject = "Password Reset OTP"
        body = f"Hello {user.username},\n\nYour OTP for password reset is: {otp}\nIt is valid for 5 minutes."
        msg = MIMEText(body)
        msg['Subject'] = subject
        msg['From'] = "noreply@winworldtech.com"
        msg['To'] = email

        smtp_server = "smtp.gmail.com"
        smtp_port = 587
        smtp_user = os.getenv('SMTP_EMAIL')
        smtp_password = os.getenv('SMTP_PASSWORD')

        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.send_message(msg)

        return jsonify({"message": "OTP sent to your email"}), 200

    except Exception as e:
        print("SMTP Error:", e)
        return jsonify({"error": "Failed to send OTP"}), 500


# ------------------ Verify OTP ------------------
@auth_bp.route("/verify-otp", methods=["POST"])
def verify_otp():
    data = request.get_json()
    email = data.get("email")
    otp = int(data.get("otp"))

    if email not in otp_storage:
        return jsonify({"error": "OTP not requested"}), 400

    if datetime.utcnow() > otp_storage[email]["expires"]:
        return jsonify({"error": "OTP expired"}), 400

    if otp != otp_storage[email]["otp"]:
        return jsonify({"error": "Invalid OTP"}), 400

    return jsonify({"message": "OTP verified"}), 200


# ------------------ Reset Password ------------------
@auth_bp.route("/reset-password", methods=["POST"])
def reset_password():
    data = request.get_json()
    email = data.get("email")
    new_password = data.get("password")

    user = User.query.filter_by(email=email).first()
    if not user:
        return jsonify({"error": "User not found"}), 404

    user.password = generate_password_hash(new_password)
    db.session.commit()

    if email in otp_storage:
        otp_storage.pop(email)

    return jsonify({"message": "Password reset successful"}), 200


#----------------------user details-------------------------------

@auth_bp.route("/users/<int:user_id>", methods=["GET"])
def get_user(user_id):
    user = User.query.get_or_404(user_id)
    return jsonify({
        "userId": user.userId,
        "username": user.username,
        "email": user.email,
        "created_at": user.created_at
    })