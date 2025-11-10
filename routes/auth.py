import os
from flask import Blueprint, request, jsonify
from extensions import db
from flask_jwt_extended import create_access_token, jwt_required, get_jwt_identity
from models.models import User
import google.auth.transport.requests
import google.oauth2.id_token
from datetime import datetime, timedelta, timezone
from utils.timezone import IST, now_ist, ensure_ist
import random
from werkzeug.security import generate_password_hash, check_password_hash
import smtplib
from email.mime.text import MIMEText
import os
from dotenv import load_dotenv
import boto3
from werkzeug.utils import secure_filename
import requests

# centralized IST helpers imported above

def send_sms(phone_number, message, shortenurl=True):
    """
    Send SMS using 2Factor API
    Args:
        phone_number (str): Recipient's phone number
        message (str): Message content
        shortenurl (bool): Whether to enable URL shortening (default: True)
    Returns:
        dict: API response
    """
    api_key = os.getenv('TWO_FACTOR_API_KEY')
    if not api_key:
        raise ValueError("TWO_FACTOR_API_KEY not found in environment variables")

    url = "https://2factor.in/API/R1/"
    params = {
        'module': 'TRANS_SMS',
        'apikey': api_key,
        'to': phone_number,
        'msg': message,
    }
    
    if shortenurl:
        params['shortenurl'] = 1

    try:
        response = requests.get(url, params=params)
        response.raise_for_status()  # Raise exception for non-200 status codes
        return response.json()
    except requests.RequestException as e:
        print(f"Error sending SMS: {str(e)}")
        raise

# ---------------------------- Blueprint ----------------------------
auth_bp = Blueprint("auth", __name__)

# ---------------------------- OTP STORAGE ----------------------------
otp_storage = {}
OTP_EXPIRY_MINUTES = 5

SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_SENDER = os.getenv("SMTP_EMAIL")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")

# Cloudflare R2 configuration (optional env overrides)
load_dotenv()
R2_ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID")
R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID")
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY")
R2_BUCKET_NAME = os.getenv("R2_BUCKET_NAME")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL")

if R2_ACCOUNT_ID and R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY and R2_BUCKET_NAME:
    R2_ENDPOINT_URL = f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
    s3_client = boto3.client(
        "s3",
        endpoint_url=R2_ENDPOINT_URL,
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
    )
else:
    s3_client = None

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
        "created_at": user.created_at,
        "profile_photo_url": user.profile_photo_url
    })

# ============================= Signup =============================

import requests
import random
from datetime import timedelta

TWO_FACTOR_API_KEY = os.getenv("TWO_FACTOR_API_KEY")

@auth_bp.route("/signup", methods=["POST", "OPTIONS"])
def signup():
    if request.method == "OPTIONS":
        return '', 204

    data = request.get_json()
    email = data.get("email")
    username = data.get("username")
    password = data.get("password")
    mobile_number = "+91" + data.get("mobile_number")

    if not all([email, username, password, mobile_number]):
        return jsonify({"error": "All fields required"}), 400

    if User.query.filter_by(email=email).first():
        return jsonify({"error": "User already exists"}), 400

    hashed_pw = generate_password_hash(password)

    # ----- Generate OTP -----
    otp = random.randint(100000, 999999)
    expiry_time = now_ist() + timedelta(minutes=OTP_EXPIRY_MINUTES)
    otp_storage[email] = {"otp": otp, "expires": expiry_time, "mobile": mobile_number , "username": username, "password": hashed_pw , "email": email}

    print(f"[{now_ist()}] OTP {otp} generated for {email} / {mobile_number}")

    # ----- Send OTP to Email -----
    try:
        subject = "Verify Your Account"
        body = (
            f"Hello {username},\n\n"
            f"Your OTP for account verification is: {otp}\n"
            f"It is valid for {OTP_EXPIRY_MINUTES} minutes."
        )
        send_email(email, subject, body)
    except Exception as e:
        print("SMTP Error:", e)
        return jsonify({"error": "Failed to send email OTP"}), 500

    # ----- Send OTP via 2Factor Custom OTP API -----
    try:
        # Format: https://2factor.in/API/V1/:api_key/SMS/:phone_number/:otp_value/:otp_template_name
        api_url = f"https://2factor.in/API/V1/{TWO_FACTOR_API_KEY}/SMS/{mobile_number}/{otp}/OTP"
        response = requests.get(api_url)
        res_data = response.json()

        if res_data.get("Status") != "Success":
            print("2Factor Error:", res_data)
            return jsonify({"error": "Failed to send mobile OTP"}), 500

        print(f"2Factor OTP Sent: {res_data}")
    except Exception as e:
        print("2Factor API Error:", e)
        return jsonify({"error": "Failed to send SMS OTP"}), 500

    # ----- Success -----
    return jsonify({
        "message": "OTP sent to both email and mobile for verification",
        "temp_user": {
            "email": email,
            "username": username,
            "password": hashed_pw,
            "mobile_number": mobile_number
        }
    }), 200

    

# ============================= Verify Signup OTP =============================
@auth_bp.route("/verify-signup-otp", methods=["POST", "OPTIONS"])
def verify_signup_otp():
    if request.method == "OPTIONS":
        return '', 200

    data = request.get_json()
    email = data.get("email")
    otp = data.get("otp")

    if not email or not otp:
        return jsonify({"error": "Email and OTP are required"}), 400

    otp_data = otp_storage.get(email)
    if not otp_data:
        return jsonify({"error": "OTP expired or not found"}), 400

    if str(otp_data["otp"]) != str(otp):
        return jsonify({"error": "Invalid OTP"}), 400

    if now_ist() > otp_data["expires"]:
        return jsonify({"error": "OTP expired"}), 400

    # ✅ Retrieve all details safely
    mobile_number = otp_data.get("mobile")
    username = otp_data.get("username")
    password = otp_data.get("password")

    if not all([mobile_number, username, password]):
        return jsonify({"error": "Missing user data"}), 400

    # Save new user
    new_user = User(
        username=username,
        email=email,
        password=password,
        mobile_number=mobile_number
    )
    db.session.add(new_user)
    db.session.commit()

    otp_storage.pop(email, None)
    access_token = create_access_token(identity=new_user.userId)

    return jsonify({
        "message": "User verified and registered successfully",
        "token": access_token,
        "user": {
            "id": new_user.userId,
            "email": new_user.email,
            "username": new_user.username
        }
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


# ---------------- Upload Profile Photo ----------------
@auth_bp.route("/upload-photo", methods=["POST"])
@jwt_required()
def upload_profile_photo():
    """Upload a profile photo to Cloudflare R2 and save the public URL to the user record."""
    if s3_client is None:
        return jsonify({"error": "Cloud storage not configured"}), 500

    try:
        user_id = int(get_jwt_identity())
        file = request.files.get("file")
        if not file:
            return jsonify({"error": "No file provided"}), 400

        ALLOWED_IMG = {"png", "jpg", "jpeg", "gif"}
        filename = secure_filename(file.filename)
        if not filename or '.' not in filename or filename.rsplit('.', 1)[1].lower() not in ALLOWED_IMG:
            return jsonify({"error": "Invalid file type"}), 400

        object_key = f"profiles/{user_id}/{filename}"

        s3_client.upload_fileobj(
            file,
            R2_BUCKET_NAME,
            object_key,
            ExtraArgs={"ContentType": file.content_type}
        )

        photo_url = f"{PUBLIC_BASE_URL}/{object_key}" if PUBLIC_BASE_URL else None

        user = User.query.get_or_404(user_id)
        user.profile_photo_url = photo_url
        db.session.commit()

        return jsonify({"msg": "Profile photo uploaded", "photo_url": photo_url}), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500
