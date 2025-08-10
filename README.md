# Dynasty Trade Analyzer

🏈 **AI-Powered Fantasy Football Trade Assistant**

A web application that provides intelligent trade suggestions for dynasty fantasy football leagues using OpenAI's ChatGPT and real data from the Sleeper API.

## ✨ Features

- **🤖 AI Trade Suggestions**: Get personalized trade recommendations powered by ChatGPT
- **📊 Trade Value Calculator**: Analyze any trade proposal with AI-powered value calculations
- **🔗 Sleeper Integration**: Seamlessly connect your Sleeper fantasy football league
- **🎯 Strategy-Based Analysis**: Customize suggestions based on your team strategy (contending, rebuilding, balanced)
- **💰 Subscription Plans**: Free plan (5 trades) and Pro plan (unlimited trades)
- **📱 Mobile Responsive**: Works perfectly on all devices
- **🔐 User Authentication**: Secure sign-up/sign-in system

## 🚀 Quick Start

### Prerequisites

- Python 3.8 or higher
- OpenAI API key (optional, will use mock responses without it)
- Sleeper fantasy football league

### Installation

1. **Clone or download the project**
   ```bash
   cd "Dynasty trade calculator"
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Set up environment variables (optional)**
   ```bash
   export OPENAI_API_KEY='your-openai-api-key-here'
   export SECRET_KEY='your-secret-key-here'
   ```

4. **Run the application**
   ```bash
   python run.py
   ```

5. **Open your browser and go to:**
   ```
   http://localhost:5000
   ```

## 🎮 How to Use

### Step 1: Choose Your Plan
- **Free Plan**: 5 AI trade suggestions per session
- **Pro Plan**: Unlimited trades and advanced features

### Step 2: Create Account
- Sign up with email and password
- Your selected plan will be automatically applied

### Step 3: Connect Your League
- Enter your Sleeper League ID (found in your league URL)
- Select your team from the league roster

### Step 4: Generate Trades
- Set your team strategy (contending, rebuilding, balanced)
- Choose your risk tolerance and position needs
- Let AI generate personalized trade suggestions

### Step 5: Analyze Trades
- Use the trade calculator to evaluate any trade proposal
- Get detailed fairness scores and AI analysis

## 📁 Project Structure

```
Dynasty trade calculator/
├── backend/
│   └── python_backend.py      # Flask application with all routes and logic
├── frontend/
│   ├── index.html             # Landing page
│   └── dynasty_trade_analyzer.html  # Legacy dashboard (now using templates)
├── templates/                 # Flask templates
│   ├── base.html             # Base template with common styling
│   ├── dashboard.html        # Main app dashboard
│   ├── trade_results.html    # Trade suggestions display
│   ├── trade_calculation_result.html  # Calculator results
│   ├── 404.html             # Error pages
│   └── 500.html
├── requirements.txt          # Python dependencies
├── run.py                   # Application startup script
├── setup.sh                # Installation script
└── README.md               # This file
```

## 🔧 Configuration

### Environment Variables

- `OPENAI_API_KEY`: Your OpenAI API key for AI-powered features
- `SECRET_KEY`: Flask secret key for session management
- `FLASK_ENV`: Set to 'development' or 'production'

### Sleeper API

The app uses the public Sleeper API to fetch:
- League information
- Team rosters
- Player data
- User information

No API key required for Sleeper integration.

## 🎯 API Integration

### OpenAI ChatGPT
- **Trade Suggestions**: Generates realistic trade proposals based on team needs
- **Trade Analysis**: Provides detailed fairness analysis and reasoning
- **Fallback**: Uses mock responses when API key is not configured

### Sleeper API
- **League Data**: Fetches real league information and rosters
- **Player Database**: Access to comprehensive NFL player data
- **Real-time**: Always uses current season data

## 💡 Technical Details

### Backend (Flask)
- **Authentication**: Session-based auth with password hashing
- **User Management**: In-memory storage (suitable for development)
- **Trade Limits**: Enforced based on subscription plan
- **API Integration**: Robust error handling and fallbacks

### Frontend
- **Responsive Design**: Mobile-first CSS with modern styling
- **Interactive UI**: Tab-based navigation and dynamic forms
- **Flash Messages**: User feedback for all actions
- **Modern Styling**: Glass-morphism effects and smooth animations

## 🚀 Production Deployment

For production use, consider:

1. **Database**: Replace in-memory storage with PostgreSQL/MySQL
2. **Environment**: Set `FLASK_ENV=production`
3. **WSGI Server**: Use Gunicorn instead of Flask dev server
4. **Security**: Use strong SECRET_KEY and HTTPS
5. **Payment Processing**: Integrate Stripe for Pro subscriptions

### Example Production Command
```bash
cd "/Users/niambashambu/Desktop/Dynasty trade calculator" && python run.py
```

## 🔒 Security Features

- Password hashing with Werkzeug
- Session management with expiration
- CSRF protection through Flask
- Input validation and sanitization
- Rate limiting through subscription plans

## 🐛 Troubleshooting

### Common Issues

1. **Module not found errors**
   - Make sure you're in the project directory
   - Install requirements: `pip install -r requirements.txt`

2. **OpenAI API errors**
   - Check if your API key is set correctly
   - App will work with mock responses if no key is provided

3. **Sleeper league connection fails**
   - Verify the league ID is correct
   - Ensure the league is public or you have access

4. **Template not found errors**
   - Make sure the `templates/` directory exists
   - Check that all template files are present

## 📞 Support

If you encounter any issues:

1. Check the console output for error messages
2. Verify all dependencies are installed
3. Ensure environment variables are set correctly
4. Check that the Sleeper league ID is valid

## 🎉 Features Coming Soon

---

**Built with ❤️ for the dynasty fantasy football community**
