#!/usr/bin/env python3
"""
Dynasty Trade Analyzer - Startup Script
"""

import os
import sys
from pathlib import Path

# Add backend to Python path
backend_path = Path(__file__).parent / 'backend'
sys.path.insert(0, str(backend_path))

# Import and run the Flask app
from python_backend import app

if __name__ == '__main__':
    # Set environment variables if not already set
    if not os.environ.get('SECRET_KEY'):
        os.environ['SECRET_KEY'] = 'dynasty-trade-analyzer-secret-key-change-in-production'
    
    if not os.environ.get('FLASK_ENV'):
        os.environ['FLASK_ENV'] = 'development'
    
    # Print startup information
    print("=" * 60)
    print("🏈 Dynasty Trade Analyzer Starting Up")
    print("=" * 60)
    print(f"📊 Access the landing page at: http://localhost:5000")
    print(f"🔑 OpenAI API Key: {'✅ Set' if os.environ.get('OPENAI_API_KEY') else '❌ Not Set'}")
    print(f"💳 Stripe Keys: {'✅ Set' if os.environ.get('STRIPE_SECRET_KEY') else '❌ Using Test Keys'}")
    print(f"🔧 Environment: {os.environ.get('FLASK_ENV', 'development')}")
    print("=" * 60)
    print()
    
    if not os.environ.get('OPENAI_API_KEY'):
        print("⚠️  WARNING: OPENAI_API_KEY not set!")
        print("   The app will use mock AI responses instead of real OpenAI API calls.")
        print("   To use real AI: Set your OpenAI API key as an environment variable:")
        print("   export OPENAI_API_KEY='your-api-key-here'")
        print()
    
    if not os.environ.get('STRIPE_SECRET_KEY'):
        print("💳 INFO: Using Stripe test keys")
        print("   For production: Set STRIPE_SECRET_KEY and STRIPE_PUBLISHABLE_KEY")
        print("   Payments will fail with test keys, but the flow will work.")
        print()
    
    # Run the Flask app
    app.run(
        host='0.0.0.0',
        port=5000,
        debug=True,
        use_reloader=True
    )
