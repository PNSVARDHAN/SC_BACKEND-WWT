from flask import Flask, jsonify
from flask_cors import CORS
from flask_jwt_extended import JWTManager
import os
from dotenv import load_dotenv
import click
from extensions import db
from routes.main import bp
from models.models import User, Device, Video, Schedule
from config import Config
from routes.auth import auth_bp
from flask_jwt_extended import JWTManager
from routes.devices import devices_bp
from routes.videos import videos_bp 
from routes.schedules import schedules_bp



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

    # Configure CORS
    CORS(
        app,
        resources={r"/*": {"origins": [
            "https://68cb8c1636f2.ngrok-free.app",
            "http://192.168.137.1:5173","*"
        ]}},

        supports_credentials=True,
        methods=["GET", "POST", "OPTIONS", "PUT", "DELETE"],
        allow_headers=["Content-Type", "Authorization"],
        expose_headers=["Content-Type", "Authorization"]
    )

    # Register blueprints
    app.register_blueprint(bp)
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(devices_bp, url_prefix="/api/devices")
    app.register_blueprint(videos_bp, url_prefix="/api/videos")
    app.register_blueprint(schedules_bp, url_prefix="/api/schedules")
    return app

if __name__ == "__main__":
    app = create_app()
    app.run(host='0.0.0.0', port=5000, debug=True)