"""
Netlify Functions entry point for Dynasty Trade Analyzer
Serverless deployment wrapper for Flask app
"""

import sys
import os

# Add backend directory to path
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', 'backend'))

from python_backend import app
from database import initialize_database
import logging

# Configure logging for production
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize database on cold start
try:
    initialize_database()
    logger.info("Database initialized for serverless function")
except Exception as e:
    logger.error(f"Failed to initialize database: {e}")

def handler(event, context):
    """Netlify Functions handler"""
    try:
        # Import WSGI adapter
        from werkzeug.wrappers import Request, Response
        
        # Convert Netlify event to WSGI environ
        environ = {
            'REQUEST_METHOD': event.get('httpMethod', 'GET'),
            'PATH_INFO': event.get('path', '/'),
            'QUERY_STRING': event.get('queryStringParameters', ''),
            'CONTENT_TYPE': event.get('headers', {}).get('content-type', ''),
            'CONTENT_LENGTH': str(len(event.get('body', ''))),
            'wsgi.input': event.get('body', ''),
            'wsgi.url_scheme': 'https',
            'SERVER_NAME': event.get('headers', {}).get('host', 'localhost'),
            'SERVER_PORT': '443',
        }
        
        # Add headers to environ
        for key, value in event.get('headers', {}).items():
            key = 'HTTP_' + key.upper().replace('-', '_')
            environ[key] = value
        
        # Call Flask app
        response = app(environ, lambda status, headers: None)
        
        # Return Netlify response format
        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'text/html'
            },
            'body': ''.join(response)
        }
        
    except Exception as e:
        logger.error(f"Function error: {e}")
        return {
            'statusCode': 500,
            'headers': {
                'Content-Type': 'application/json'
            },
            'body': '{"error": "Internal server error"}'
        }
