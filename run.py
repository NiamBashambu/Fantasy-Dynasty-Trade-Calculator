#!/usr/bin/env python3
"""Local development entry point. In production use gunicorn (see wsgi.py)."""

import os

from dynasty import create_app

app = create_app()

if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5000"))
    print(f"Dynasty Trade Analyzer: http://{host}:{port}")
    print(f"AI explanations: {'on' if app.config.get('OPENAI_API_KEY') else 'off (set OPENAI_API_KEY to enable)'}")
    app.run(host=host, port=port, debug=app.config["DEBUG"])
