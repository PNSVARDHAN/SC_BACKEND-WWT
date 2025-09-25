import os
from urllib.parse import quote_plus

def construct_database_url():
    db_type = os.getenv('DB_TYPE', 'sqlite')
    
    if db_type == 'sqlite':
        db_name = os.getenv('DB_NAME', 'event_database.db')
        return f'sqlite:///{db_name}'
    
    # For MySQL or PostgreSQL
    db_user = os.getenv('DB_USER')
    db_password = quote_plus(os.getenv('DB_PASSWORD', ''))  
    db_host = os.getenv('DB_HOST', 'localhost')
    db_port = os.getenv('DB_PORT')
    db_name = os.getenv('DB_NAME')
    
    if db_type == 'mysql':
        return f'mysql+pymysql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}'
    elif db_type == 'postgresql':
        return f'postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}'
    
    raise ValueError(f'Unsupported database type: {db_type}')

class Config:
    # Flask
    SECRET_KEY = os.getenv('FLASK_SECRET_KEY', 'dev')
    
    # Database
    SQLALCHEMY_DATABASE_URI = os.getenv('DATABASE_URL') or construct_database_url()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # Google OAuth
    GOOGLE_CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID')
    GOOGLE_CLIENT_SECRET = os.getenv('GOOGLE_CLIENT_SECRET')

    # jwt token 
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "default-jwt-secret")
    JWT_TOKEN_LOCATION = ["headers"]
    JWT_HEADER_NAME = "Authorization"
    JWT_HEADER_TYPE = "Bearer"
    JWT_ACCESS_TOKEN_EXPIRES = False  # Tokens don't expire
    JWT_IDENTITY_CLAIM = 'sub'  # Use standard JWT claim name
    JWT_ERROR_MESSAGE_KEY = 'error'  # Key for error messages
