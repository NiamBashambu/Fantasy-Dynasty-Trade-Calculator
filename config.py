"""
Configuration settings for Dynasty Trade Analyzer
Supports development and production environments
"""

import os
from pathlib import Path

class Config:
    """Base configuration"""
    
    # Flask Settings
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
    DEBUG = False
    TESTING = False
    
    # Database Settings
    DATABASE_URL = os.environ.get('DATABASE_URL')
    DB_HOST = os.environ.get('DB_HOST', 'localhost')
    DB_PORT = os.environ.get('DB_PORT', '5432')
    DB_NAME = os.environ.get('DB_NAME', 'dynasty_trade_analyzer')
    DB_USER = os.environ.get('DB_USER', 'postgres')
    DB_PASSWORD = os.environ.get('DB_PASSWORD', '')
    
    # API Keys
    OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')
    SLEEPER_BASE_URL = 'https://api.sleeper.app/v1'
    
    # Stripe Settings
    STRIPE_PUBLISHABLE_KEY = os.environ.get('STRIPE_PUBLISHABLE_KEY', 'pk_test_your_publishable_key_here')
    STRIPE_SECRET_KEY = os.environ.get('STRIPE_SECRET_KEY', 'sk_test_your_secret_key_here')
    STRIPE_ENDPOINT_SECRET = os.environ.get('STRIPE_ENDPOINT_SECRET')  # For webhooks
    
    # Session Settings
    SESSION_DURATION_HOURS = 24
    PERMANENT_SESSION_LIFETIME = 24 * 60 * 60  # 24 hours in seconds
    
    # Trade Limits
    FREE_PLAN_TRADE_LIMIT = 5
    PRO_PLAN_PRICE = 500  # $5.00 in cents
    
    # Security Settings
    PASSWORD_MIN_LENGTH = 8
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'

class DevelopmentConfig(Config):
    """Development configuration"""
    DEBUG = True
    DB_NAME = 'dynasty_trade_analyzer_dev'
    SESSION_COOKIE_SECURE = False  # Allow HTTP in development

class ProductionConfig(Config):
    """Production configuration"""
    DEBUG = False
    
    # Ensure required environment variables are set
    @classmethod
    def validate_config(cls):
        required_vars = [
            'SECRET_KEY',
            'DATABASE_URL',
            'OPENAI_API_KEY',
            'STRIPE_SECRET_KEY',
            'STRIPE_PUBLISHABLE_KEY'
        ]
        
        missing_vars = [var for var in required_vars if not os.environ.get(var)]
        
        if missing_vars:
            raise ValueError(f"Missing required environment variables: {', '.join(missing_vars)}")

class TestingConfig(Config):
    """Testing configuration"""
    TESTING = True
    DB_NAME = 'dynasty_trade_analyzer_test'
    WTF_CSRF_ENABLED = False

# Configuration mapping
config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    'default': DevelopmentConfig
}

def get_config():
    """Get configuration based on environment"""
    env = os.environ.get('FLASK_ENV', 'development')
    return config.get(env, config['default'])
