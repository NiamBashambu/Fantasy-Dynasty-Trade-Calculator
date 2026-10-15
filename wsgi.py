"""Production entry point: gunicorn wsgi:app"""

from dynasty import create_app

app = create_app()
