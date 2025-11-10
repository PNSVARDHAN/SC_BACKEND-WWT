from flask import Flask, jsonify
from flask_cors import CORS
from flask_jwt_extended import JWTManager
import os
from dotenv import load_dotenv
import click
from extensions import db
from flask_migrate import Migrate
from routes.main import bp
from models.models import User, Device, Video, Schedule
from config import Config
from routes.auth import auth_bp
from flask_jwt_extended import JWTManager
from routes.devices import devices_bp
from routes.videos import videos_bp 
from routes.schedules import schedules_bp
from werkzeug.middleware.proxy_fix import ProxyFix
import logging
from logging.handlers import RotatingFileHandler



# Load environment variables
load_dotenv()

def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)

    if test_config is None:
        # Load config from Config class
        app.config.from_object(Config)
    else:
        app.config.update(test_config)

    try:
        os.makedirs(app.instance_path)
    except OSError:
        pass

    # Initialize Flask extensions
    db.init_app(app)
    jwt = JWTManager(app)
    Migrate(app, db)

    # Trust proxy headers (Nginx/ALB)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)

    @jwt.invalid_token_loader
    def invalid_token_callback(error_string):
        return jsonify({
            'msg': 'Invalid token',
            'error': str(error_string)
        }), 422

    @jwt.expired_token_loader
    def expired_token_callback(jwt_header, jwt_data):
        return jsonify({
            'msg': 'Token has expired',
            'error': 'token_expired'
        }), 401


    # Register CLI commands
    @click.command('init-db')
    def init_db_command():
        """Command for initializing the database"""
        with app.app_context():
            db.create_all()
            click.echo('Database created successfully')

    app.cli.add_command(init_db_command)

    # Configure CORS (set CORS_ORIGINS env, comma-separated); default to '*'
    cors_origins = os.getenv("CORS_ORIGINS","*")
    origins_list = [o.strip() for o in cors_origins.split(",") if o.strip()]
    CORS(
        app,
        resources={r"/*": {"origins": origins_list}},
        supports_credentials=True,
        methods=["GET", "POST", "OPTIONS", "PUT", "DELETE"],
        allow_headers=["Content-Type", "Authorization"],
        expose_headers=["Content-Type", "Authorization"]
    )

    # Logging (rotating file + stderr) in non-debug
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    app.logger.setLevel(getattr(logging, log_level, logging.INFO))
    if not app.debug and not app.testing:
        log_dir = os.getenv("LOG_DIR", "/var/log/app")
        try:
            os.makedirs(log_dir, exist_ok=True)
            file_handler = RotatingFileHandler(os.path.join(log_dir, "app.log"), maxBytes=5*1024*1024, backupCount=5)
            formatter = logging.Formatter('%(asctime)s %(levelname)s [%(name)s] %(message)s')
            file_handler.setFormatter(formatter)
            file_handler.setLevel(getattr(logging, log_level, logging.INFO))
            app.logger.addHandler(file_handler)
        except Exception:
            pass

    # Register blueprints
    app.register_blueprint(bp)
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(devices_bp, url_prefix="/api/devices")
    app.register_blueprint(videos_bp, url_prefix="/api/videos")
    app.register_blueprint(schedules_bp, url_prefix="/api/schedules")

    # Health and readiness endpoints
    @app.route("/health", methods=["GET"])  # for container/ALB health checks
    def health():
        return jsonify({"status": "ok"}), 200

    @app.route("/ready", methods=["GET"])  # DB ping
    def ready():
        try:
            db.session.execute(db.text("SELECT 1"))
            return jsonify({"status": "ready"}), 200
        except Exception as e:
            app.logger.exception("Readiness check failed")
            return jsonify({"status": "unhealthy", "error": str(e)}), 500

    # Standard JSON error handlers for production
    @app.errorhandler(404)
    def not_found(e):
        return jsonify({"error": "Not Found"}), 404

    @app.errorhandler(413)
    def payload_too_large(e):
        return jsonify({"error": "Payload too large"}), 413

    @app.errorhandler(Exception)
    def unhandled_exception(e):
        app.logger.exception("Unhandled exception")
        return jsonify({"error": "Internal Server Error"}), 500
    return app

if __name__ == "__main__":
    app = create_app()
    app.run(host='0.0.0.0', port=5000, debug=True)