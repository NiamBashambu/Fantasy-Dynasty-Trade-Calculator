from flask import Flask, render_template, request, jsonify, redirect, url_for, session, flash, send_from_directory
import requests
import json
import os
from datetime import datetime, timedelta
from openai import OpenAI
import stripe
from dataclasses import dataclass
from typing import List, Dict, Optional, Any
import logging
import hashlib
import secrets
from werkzeug.security import generate_password_hash, check_password_hash

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__, template_folder='../templates')
app.secret_key = os.environ.get('SECRET_KEY', 'your-secret-key-here')

# Configuration
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')
openai_client = None
if OPENAI_API_KEY:
    openai_client = OpenAI(api_key=OPENAI_API_KEY)

# Stripe Configuration
STRIPE_PUBLISHABLE_KEY = os.environ.get('STRIPE_PUBLISHABLE_KEY', 'pk_test_your_publishable_key_here')
STRIPE_SECRET_KEY = os.environ.get('STRIPE_SECRET_KEY', 'sk_test_your_secret_key_here')
stripe.api_key = STRIPE_SECRET_KEY

# Sleeper API endpoints
SLEEPER_BASE_URL = "https://api.sleeper.app/v1"

# In-memory user storage (in production, use a proper database)
users_db = {}
sessions_db = {}

@dataclass
class User:
    user_id: str
    email: str
    name: str
    password_hash: str
    plan: str  # 'free' or 'pro'
    trade_count: int
    created_at: datetime
    last_login: Optional[datetime] = None

@dataclass
class Player:
    player_id: str
    name: str
    position: str
    team: str
    age: Optional[int] = None
    experience: Optional[int] = None

@dataclass
class League:
    league_id: str
    name: str
    total_rosters: int
    settings: Dict[str, Any]
    season: str
    users: List[Dict[str, Any]]
    rosters: List[Dict[str, Any]]

@dataclass
class TradePreferences:
    strategy: str  # contend, rebuild, balanced
    risk_tolerance: str  # low, medium, high
    position_needs: List[str]
    additional_notes: str

class AuthManager:
    """Handle user authentication and session management"""
    
    @staticmethod
    def create_user(email: str, name: str, password: str, plan: str = 'free') -> Optional[User]:
        """Create a new user account"""
        
        # Check if user already exists
        for user in users_db.values():
            if user.email.lower() == email.lower():
                return None
        
        # Generate user ID and hash password
        user_id = secrets.token_urlsafe(16)
        password_hash = generate_password_hash(password)
        
        # Create user
        user = User(
            user_id=user_id,
            email=email.lower(),
            name=name,
            password_hash=password_hash,
            plan=plan,
            trade_count=0,
            created_at=datetime.now()
        )
        
        users_db[user_id] = user
        logger.info(f"Created new user: {email} with plan: {plan}")
        return user
    
    @staticmethod
    def authenticate_user(email: str, password: str) -> Optional[User]:
        """Authenticate user login"""
        
        for user in users_db.values():
            if user.email.lower() == email.lower():
                if check_password_hash(user.password_hash, password):
                    user.last_login = datetime.now()
                    logger.info(f"User authenticated: {email}")
                    return user
                break
        
        logger.warning(f"Authentication failed for: {email}")
        return None
    
    @staticmethod
    def create_session(user: User) -> str:
        """Create a user session"""
        session_token = secrets.token_urlsafe(32)
        sessions_db[session_token] = {
            'user_id': user.user_id,
            'created_at': datetime.now(),
            'expires_at': datetime.now() + timedelta(days=7)
        }
        return session_token
    
    @staticmethod
    def get_user_from_session(session_token: str) -> Optional[User]:
        """Get user from session token"""
        if session_token in sessions_db:
            session_data = sessions_db[session_token]
            
            # Check if session expired
            if datetime.now() > session_data['expires_at']:
                del sessions_db[session_token]
                return None
            
            user_id = session_data['user_id']
            return users_db.get(user_id)
        
        return None
    
    @staticmethod
    def logout_user(session_token: str):
        """Log out user by removing session"""
        if session_token in sessions_db:
            del sessions_db[session_token]

class SleeperAPI:
    """Wrapper for Sleeper API calls"""
    
    @staticmethod
    def get_league(league_id: str) -> Optional[Dict[str, Any]]:
        """Get league information"""
        try:
            url = f"{SLEEPER_BASE_URL}/league/{league_id}"
            logger.info(f"Fetching league data from: {url}")
            response = requests.get(url)
            response.raise_for_status()
            data = response.json()
            logger.info(f"League data received: {data.get('name', 'Unknown')} ({data.get('total_rosters', 0)} teams)")
            return data
        except requests.RequestException as e:
            logger.error(f"Error fetching league {league_id}: {e}")
            logger.error(f"Response status: {response.status_code if 'response' in locals() else 'No response'}")
            return None
    
    @staticmethod
    def get_league_users(league_id: str) -> Optional[List[Dict[str, Any]]]:
        """Get league users"""
        try:
            response = requests.get(f"{SLEEPER_BASE_URL}/league/{league_id}/users")
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logger.error(f"Error fetching league users {league_id}: {e}")
            return None
    
    @staticmethod
    def get_league_rosters(league_id: str) -> Optional[List[Dict[str, Any]]]:
        """Get league rosters"""
        try:
            response = requests.get(f"{SLEEPER_BASE_URL}/league/{league_id}/rosters")
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logger.error(f"Error fetching league rosters {league_id}: {e}")
            return None
    
    @staticmethod
    def get_all_players() -> Optional[Dict[str, Any]]:
        """Get all NFL players data"""
        try:
            response = requests.get(f"{SLEEPER_BASE_URL}/players/nfl")
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logger.error(f"Error fetching players: {e}")
            return None

class TradeAnalyzer:
    """AI-powered trade analysis using OpenAI"""
    
    def __init__(self, api_key: str = None):
        # API key is handled globally now
        pass
    
    def generate_trade_suggestions(
        self, 
        league_data: League, 
        user_roster: Dict[str, Any],
        all_players: Dict[str, Any],
        preferences: TradePreferences,
        max_suggestions: int = 5
    ) -> List[Dict[str, Any]]:
        """Generate AI-powered trade suggestions"""
        
        if not openai_client:
            # Return mock trades if no OpenAI key
            return self._generate_mock_trades(league_data, user_roster, preferences)
        
        try:
            # Prepare context for AI
            context = self._prepare_trade_context(league_data, user_roster, all_players, preferences)
            
            prompt = f"""
            You are an expert dynasty fantasy football trade analyzer. Based on the following context, suggest {max_suggestions} realistic and fair trades.
            
            Context:
            {context}
            
            User Preferences:
            - Strategy: {preferences.strategy}
            - Risk Tolerance: {preferences.risk_tolerance}
            - Position Needs: {', '.join(preferences.position_needs) if preferences.position_needs else 'None specified'}
            - Additional Notes: {preferences.additional_notes or 'None'}
            
            For each trade suggestion, provide:
            1. The specific players/picks being traded
            2. Which team receives what
            3. A fairness score (0-100)
            4. Detailed reasoning explaining why this trade makes sense
            
            Format your response as JSON with this structure:
            {{
                "trades": [
                    {{
                        "id": 1,
                        "fairness_score": 85,
                        "user_gives": ["Player Name 1", "2025 2nd Round Pick"],
                        "user_receives": ["Player Name 2", "Player Name 3"],
                        "trade_partner": "Team Name",
                        "reasoning": "Detailed explanation of why this trade works"
                    }}
                ]
            }}
            
            Make sure trades are realistic, fair, and align with the user's stated preferences.
            """
            
            response = openai_client.chat.completions.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": "You are an expert dynasty fantasy football analyst."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=2000,
                temperature=0.7
            )
            
            # Parse AI response
            ai_response = response.choices[0].message.content
            trades_data = json.loads(ai_response)
            return trades_data.get('trades', [])
            
        except Exception as e:
            logger.error(f"Error generating AI trades: {e}")
            return self._generate_mock_trades(league_data, user_roster, preferences)
    
    def _prepare_trade_context(
        self, 
        league_data: League, 
        user_roster: Dict[str, Any],
        all_players: Dict[str, Any],
        preferences: TradePreferences
    ) -> str:
        """Prepare context string for AI analysis"""
        
        # Get user's players
        user_players = []
        if user_roster.get('players'):
            for player_id in user_roster['players']:
                if player_id in all_players:
                    player = all_players[player_id]
                    user_players.append(f"{player.get('full_name', 'Unknown')} ({player.get('position', 'N/A')})")
        
        # Get other teams' rosters
        other_teams = []
        for roster in league_data.rosters:
            if roster['roster_id'] != user_roster['roster_id']:
                team_players = []
                if roster.get('players'):
                    for player_id in roster['players'][:10]:  # Limit for context
                        if player_id in all_players:
                            player = all_players[player_id]
                            team_players.append(f"{player.get('full_name', 'Unknown')} ({player.get('position', 'N/A')})")
                
                # Find team owner name
                owner_name = "Unknown Team"
                for user in league_data.users:
                    if user['user_id'] == roster.get('owner_id'):
                        owner_name = user.get('display_name', 'Unknown Team')
                        break
                
                other_teams.append({
                    'name': owner_name,
                    'players': team_players[:10]
                })
        
        context = f"""
        League: {league_data.name}
        User's Current Roster: {', '.join(user_players[:15])}
        
        Other Teams Available for Trades:
        """
        
        for team in other_teams[:5]:  # Limit to 5 teams for context
            context += f"\n{team['name']}: {', '.join(team['players'][:8])}"
        
        return context
    
    def _generate_mock_trades(
        self, 
        league_data: League, 
        user_roster: Dict[str, Any],
        preferences: TradePreferences
    ) -> List[Dict[str, Any]]:
        """Generate mock trades when OpenAI is not available"""
        
        mock_trades = [
            {
                "id": 1,
                "fairness_score": 92,
                "user_gives": ["Christian McCaffrey", "2025 2nd Round Pick"],
                "user_receives": ["Ja'Marr Chase", "Tony Pollard"],
                "trade_partner": "Dynasty Warriors",
                "reasoning": "This trade helps you get younger at WR while maintaining RB depth. Chase is an elite long-term asset perfect for dynasty. You're trading peak value CMC for sustained production."
            },
            {
                "id": 2,
                "fairness_score": 88,
                "user_gives": ["Travis Kelce", "Derrick Henry"],
                "user_receives": ["Kyle Pitts", "Breece Hall", "2025 1st Round Pick"],
                "trade_partner": "Championship Chasers",
                "reasoning": "Perfect rebuild move if that's your strategy. You're trading aging veterans for young talent with massive upside. Pitts could return to elite form, Hall is a stud RB."
            },
            {
                "id": 3,
                "fairness_score": 85,
                "user_gives": ["Cooper Kupp", "2024 3rd Round Pick"],
                "user_receives": ["DK Metcalf", "Rachaad White"],
                "trade_partner": "Fantasy Fanatics",
                "reasoning": "Age-based swap that gives you a younger WR1 in Metcalf plus RB depth. Kupp is still elite but Metcalf has more dynasty runway ahead."
            }
        ]
        
        # Filter based on strategy
        if preferences.strategy == "rebuild":
            return [trade for trade in mock_trades if "young" in trade["reasoning"] or "rebuild" in trade["reasoning"]][:3]
        elif preferences.strategy == "contend":
            return [trade for trade in mock_trades if "contend" in trade["reasoning"] or "win now" in trade["reasoning"]][:3]
        
        return mock_trades[:3]
    
    def calculate_trade_value(
        self, 
        teamA_players: List[str], 
        teamB_players: List[str],
        all_players: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Calculate trade value between two sets of players"""
        
        if not openai_client:
            return self._mock_trade_calculation(teamA_players, teamB_players)
        
        try:
            prompt = f"""
            Analyze this fantasy football trade and provide a detailed breakdown:
            
            Team A gives: {', '.join(teamA_players)}
            Team B gives: {', '.join(teamB_players)}
            
            Provide a JSON response with:
            {{
                "teamA_value": numeric_value,
                "teamB_value": numeric_value,
                "fairness_score": 0-100,
                "winner": "Team A", "Team B", or "Even",
                "analysis": "detailed explanation",
                "recommendations": "suggestions to balance if needed"
            }}
            
            Consider current player values, age, injury history, and dynasty relevance.
            """
            
            response = openai_client.chat.completions.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": "You are an expert dynasty fantasy football analyst."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=1000,
                temperature=0.3
            )
            
            return json.loads(response.choices[0].message.content)
            
        except Exception as e:
            logger.error(f"Error calculating trade value: {e}")
            return self._mock_trade_calculation(teamA_players, teamB_players)
    
    def _mock_trade_calculation(self, teamA_players: List[str], teamB_players: List[str]) -> Dict[str, Any]:
        """Mock trade calculation when OpenAI is not available"""
        
        # Simple mock calculation based on player count and mock values
        teamA_value = len(teamA_players) * 50 + (len(teamA_players) * 10)  # Mock calculation
        teamB_value = len(teamB_players) * 55 + (len(teamB_players) * 8)   # Mock calculation
        
        difference = abs(teamA_value - teamB_value)
        fairness_score = max(0, 100 - (difference / max(teamA_value, teamB_value)) * 100)
        
        if fairness_score >= 90:
            winner = "Even"
        elif teamA_value > teamB_value:
            winner = "Team A"
        else:
            winner = "Team B"
        
        return {
            "teamA_value": teamA_value,
            "teamB_value": teamB_value,
            "fairness_score": round(fairness_score),
            "winner": winner,
            "analysis": f"Team A offers {len(teamA_players)} player(s) with estimated value of {teamA_value}. Team B offers {len(teamB_players)} player(s) with estimated value of {teamB_value}. The trade favors {winner}.",
            "recommendations": "Consider adding draft picks or additional players to balance the trade if needed."
        }

class DynastyTradeApp:
    """Main application class"""
    
    def __init__(self):
        self.trade_analyzer = TradeAnalyzer(OPENAI_API_KEY)
        self.all_players_cache = None
        self.cache_timestamp = None
    
    def get_all_players(self) -> Dict[str, Any]:
        """Get all players with caching"""
        now = datetime.now()
        
        # Cache players for 1 hour
        if (self.all_players_cache is None or 
            self.cache_timestamp is None or 
            (now - self.cache_timestamp).seconds > 3600):
            
            logger.info("Fetching fresh player data from Sleeper API")
            players_data = SleeperAPI.get_all_players()
            
            if players_data:
                self.all_players_cache = players_data
                self.cache_timestamp = now
            else:
                logger.warning("Failed to fetch players, using cached data")
        
        return self.all_players_cache or {}
    
    def connect_league(self, league_id: str) -> Optional[League]:
        """Connect to a Sleeper league"""
        
        # Get league info
        league_data = SleeperAPI.get_league(league_id)
        if not league_data:
            return None
        
        # Get users
        users = SleeperAPI.get_league_users(league_id)
        if not users:
            return None
        
        # Get rosters
        rosters = SleeperAPI.get_league_rosters(league_id)
        if not rosters:
            return None
        
        return League(
            league_id=league_id,
            name=league_data.get('name', 'Unknown League'),
            total_rosters=league_data.get('total_rosters', 0),
            settings=league_data.get('settings', {}),
            season=league_data.get('season', '2025'),
            users=users,
            rosters=rosters
        )
    
    def get_user_roster(self, league: League, user_id: str) -> Optional[Dict[str, Any]]:
        """Get specific user's roster"""
        for roster in league.rosters:
            if roster.get('owner_id') == user_id:
                return roster
        return None

# Initialize the app
dynasty_app = DynastyTradeApp()

# Helper function to check authentication
def require_auth(f):
    """Decorator to require authentication"""
    def decorated_function(*args, **kwargs):
        session_token = session.get('session_token')
        if session_token:
            user = AuthManager.get_user_from_session(session_token)
            if user:
                session['user'] = {
                    'user_id': user.user_id,
                    'email': user.email,
                    'name': user.name,
                    'plan': user.plan,
                    'trade_count': user.trade_count
                }
                return f(*args, **kwargs)
        
        flash('Please sign in to access this feature', 'error')
        return redirect(url_for('landing'))
    
    decorated_function.__name__ = f.__name__
    return decorated_function

@app.route('/')
def landing():
    """Landing page"""
    return send_from_directory('../frontend', 'index.html')

@app.route('/app')
@require_auth
def app_dashboard():
    """Main application dashboard"""
    return render_template('dashboard.html')

@app.route('/auth', methods=['POST'])
def authenticate():
    """Handle authentication (sign in/sign up)"""
    mode = request.form.get('mode', 'signin')
    email = request.form.get('email', '').strip()
    password = request.form.get('password', '').strip()
    plan = request.form.get('plan', 'free')
    
    # Debug logging
    logger.info(f"Auth request - Mode: {mode}, Email: {email}, Plan: {plan}")
    
    if not email or not password:
        flash('Email and password are required', 'error')
        return redirect(url_for('landing'))
    
    if mode == 'signup':
        name = request.form.get('name', '').strip()
        if not name:
            flash('Name is required for sign up', 'error')
            return redirect(url_for('landing'))
        
        # Create new user
        user = AuthManager.create_user(email, name, password, plan)
        if not user:
            flash('An account with this email already exists', 'error')
            return redirect(url_for('landing'))
        
        # Create session
        session_token = AuthManager.create_session(user)
        session['session_token'] = session_token
        session['user'] = {
            'user_id': user.user_id,
            'email': user.email,
            'name': user.name,
            'plan': user.plan,
            'trade_count': user.trade_count
        }
        
        flash(f'Account created successfully! Welcome to {plan.title()} plan.', 'success')
        return redirect(url_for('app_dashboard'))
    
    else:  # signin
        user = AuthManager.authenticate_user(email, password)
        if not user:
            # Check if user exists but wrong password
            user_exists = any(u.email.lower() == email.lower() for u in users_db.values())
            if user_exists:
                return redirect(url_for('landing') + '?error=1&message=' + 'Invalid password. Please try again.')
            else:
                return redirect(url_for('landing') + '?error=1&message=' + 'No account found with this email. Please sign up first.')
        
        # Create session
        session_token = AuthManager.create_session(user)
        session['session_token'] = session_token
        session['user'] = {
            'user_id': user.user_id,
            'email': user.email,
            'name': user.name,
            'plan': user.plan,
            'trade_count': user.trade_count
        }
        
        flash(f'Welcome back, {user.name}!', 'success')
        return redirect(url_for('app_dashboard'))

@app.route('/logout')
def logout():
    """Log out user"""
    session_token = session.get('session_token')
    if session_token:
        AuthManager.logout_user(session_token)
    
    session.clear()
    flash('You have been logged out', 'info')
    return redirect(url_for('landing'))

@app.route('/connect-league', methods=['POST'])
@require_auth
def connect_league():
    """Connect to Sleeper league"""
    league_id = request.form.get('league_id', '').strip()
    
    logger.info(f"Attempting to connect to league: {league_id}")
    
    if not league_id:
        flash('Please enter a valid league ID', 'error')
        logger.warning("No league ID provided")
        return redirect(url_for('league_setup'))
    
    # Connect to league
    league = dynasty_app.connect_league(league_id)
    
    if not league:
        flash('Failed to connect to league. Please check your league ID and ensure it exists.', 'error')
        logger.error(f"Failed to connect to league: {league_id}")
        return redirect(url_for('league_setup'))
    
    # Store minimal league data in session (avoid cookie size limit)
    session['league_data'] = {
        'league_id': league.league_id,
        'name': league.name,
        'total_rosters': league.total_rosters,
        'connected': True
    }
    
    # Store full league data in memory cache for this session
    if not hasattr(dynasty_app, 'league_cache'):
        dynasty_app.league_cache = {}
    dynasty_app.league_cache[league.league_id] = league
    
    logger.info(f"Successfully connected to league: {league.name} (ID: {league_id})")
    flash('League connected successfully!', 'success')
    return redirect(url_for('league_setup'))

@app.route('/league-setup')
@require_auth
def league_setup():
    """League setup page with team selection"""
    # Get full league data if connected
    full_league = None
    if 'league_data' in session:
        full_league = get_league_from_cache()
    
    return render_template('league_setup.html', full_league=full_league)

@app.route('/generate-trades-page')
@require_auth
def generate_trades_page():
    """Direct link to trade generation"""
    if not session.get('league_data') or not session.get('selected_team'):
        flash('Please complete league setup first', 'error')
        return redirect(url_for('league_setup'))
    
    return redirect(url_for('app_dashboard') + '#generator')

@app.route('/select-team', methods=['POST'])
@require_auth
def select_team():
    """Select user's team"""
    team_id = request.form.get('team_id')
    league_data = session.get('league_data')
    
    if not team_id or not league_data:
        flash('Please select a valid team', 'error')
        return redirect(url_for('league_setup'))
    
    # Get full league data from cache
    full_league = get_league_from_cache()
    if not full_league:
        flash('League data not found. Please reconnect to your league.', 'error')
        return redirect(url_for('league_setup'))
    
    # Find user info
    user_info = None
    for user in full_league.users:
        if user['user_id'] == team_id:
            user_info = user
            break
    
    if not user_info:
        flash('Invalid team selection', 'error')
        return redirect(url_for('app_dashboard'))
    
    # Store selected team
    session['selected_team'] = {
        'user_id': team_id,
        'display_name': user_info.get('display_name', 'Your Team'),
        'avatar': user_info.get('avatar')
    }
    
    flash(f'Team "{user_info.get("display_name")}" selected successfully!', 'success')
    return redirect(url_for('league_setup'))

@app.route('/trade-generator')
@require_auth
def trade_generator():
    """Trade generator page"""
    if not session.get('league_data') or not session.get('selected_team'):
        flash('Please complete league setup first', 'error')
        return redirect(url_for('app_dashboard'))
    
    return render_template('dashboard.html')

@app.route('/generate-trades', methods=['POST'])
@require_auth
def generate_trades():
    """Generate AI trade suggestions"""
    league_data = session.get('league_data')
    selected_team = session.get('selected_team')
    user_data = session.get('user')
    
    if not league_data or not selected_team or not user_data:
        flash('Please complete league setup first', 'error')
        return redirect(url_for('app_dashboard'))
    
    # Get current user from database
    session_token = session.get('session_token')
    current_user = AuthManager.get_user_from_session(session_token)
    
    if not current_user:
        flash('Session expired. Please sign in again.', 'error')
        return redirect(url_for('landing'))
    
    # Check trade limits
    if current_user.plan == 'free' and current_user.trade_count >= 5:
        flash('You have reached the limit of 5 trades for free plan. Upgrade to Pro for unlimited trades.', 'error')
        return redirect(url_for('trade_generator'))
    
    # Get form data
    preferences = TradePreferences(
        strategy=request.form.get('strategy', 'balanced'),
        risk_tolerance=request.form.get('risk_tolerance', 'medium'),
        position_needs=request.form.getlist('position_needs'),
        additional_notes=request.form.get('additional_notes', '')
    )
    
    # Get full league data from cache
    full_league = get_league_from_cache()
    if not full_league:
        flash('League data not found. Please reconnect to your league.', 'error')
        return redirect(url_for('league_setup'))
    
    # Use the full league object
    league = full_league
    
    # Get user's roster
    user_roster = dynasty_app.get_user_roster(league, selected_team['user_id'])
    if not user_roster:
        flash('Could not find your roster', 'error')
        return redirect(url_for('trade_generator'))
    
    # Get all players
    all_players = dynasty_app.get_all_players()
    
    # Generate trades
    max_suggestions = 10 if current_user.plan == 'pro' else 5
    trades = dynasty_app.trade_analyzer.generate_trade_suggestions(
        league, user_roster, all_players, preferences, max_suggestions
    )
    
    # Update trade count in database (in-memory for now)
    current_user.trade_count += 1
    
    # Update the in-memory user database
    if current_user.email in users_db:
        users_db[current_user.email].trade_count = current_user.trade_count
    
    # Update session
    session['user']['trade_count'] = current_user.trade_count
    
    return render_template('trade_results.html', 
                         trades=trades,
                         league=league_data,
                         team=selected_team)

@app.route('/trade-calculator')
@require_auth
def trade_calculator():
    """Trade calculator page"""
    return render_template('dashboard.html')

@app.route('/calculate-trade', methods=['POST'])
@require_auth
def calculate_trade():
    """Calculate trade value"""
    teamA_players_text = request.form.get('teamA_players', '').strip()
    teamB_players_text = request.form.get('teamB_players', '').strip()
    
    if not teamA_players_text or not teamB_players_text:
        flash('Please enter players for both teams', 'error')
        return redirect(url_for('app_dashboard') + '#calculator')
    
    # Parse player lists
    teamA_players = [p.strip() for p in teamA_players_text.split('\n') if p.strip()]
    teamB_players = [p.strip() for p in teamB_players_text.split('\n') if p.strip()]
    
    # Validate that we have actual players after parsing
    if not teamA_players or not teamB_players:
        flash('Please enter at least one player for each team', 'error')
        return redirect(url_for('app_dashboard') + '#calculator')
    
    # Get all players data
    all_players = dynasty_app.get_all_players()
    
    # Calculate trade
    result = dynasty_app.trade_analyzer.calculate_trade_value(
        teamA_players, teamB_players, all_players
    )
    
    return render_template('trade_calculation_result.html',
                         result=result,
                         teamA_players=teamA_players,
                         teamB_players=teamB_players)

@app.route('/update-plan', methods=['POST'])
@require_auth
def update_plan():
    """Update subscription plan"""
    plan = request.form.get('plan', 'free')
    
    # Get current user
    session_token = session.get('session_token')
    current_user = AuthManager.get_user_from_session(session_token)
    
    if not current_user:
        flash('Session expired. Please sign in again.', 'error')
        return redirect(url_for('landing'))
    
    # Update user plan
    current_user.plan = plan
    current_user.trade_count = 0  # Reset count on plan change
    
    # Update the in-memory user database
    if current_user.email in users_db:
        users_db[current_user.email].plan = plan
        users_db[current_user.email].trade_count = 0
    
    # Update session
    session['user']['plan'] = plan
    session['user']['trade_count'] = 0
    
    flash(f'Plan updated to {plan.title()}!', 'success')
    return redirect(url_for('app_dashboard'))

@app.route('/share-trade/<int:trade_id>')
@require_auth
def share_trade(trade_id):
    """Share a trade suggestion"""
    flash('Trade sharing feature coming soon!', 'info')
    return redirect(url_for('trade_generator'))

@app.route('/refine-trade/<int:trade_id>')
@require_auth
def refine_trade(trade_id):
    """Refine a trade suggestion"""
    flash('Trade refinement feature coming soon!', 'info')
    return redirect(url_for('trade_generator'))

@app.route('/upgrade')
@require_auth
def upgrade():
    """Show upgrade to Pro plan page"""
    return render_template('upgrade.html', stripe_key=STRIPE_PUBLISHABLE_KEY)

@app.route('/create-checkout-session', methods=['POST'])
@require_auth
def create_checkout_session():
    """Create Stripe checkout session for Pro upgrade"""
    try:
        # Get current user
        session_token = session.get('session_token')
        current_user = AuthManager.get_user_from_session(session_token)
        
        if not current_user:
            flash('Session expired. Please sign in again.', 'error')
            return redirect(url_for('landing'))
        
        # Create Stripe checkout session
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'usd',
                    'product_data': {
                        'name': 'Dynasty Trade Analyzer Pro',
                        'description': 'Unlimited AI trade suggestions and advanced features - Only $5/month!'
                    },
                    'unit_amount': 500,  # $5.00 in cents
                    'recurring': {
                        'interval': 'month'
                    }
                },
                'quantity': 1,
            }],
            mode='subscription',
            success_url=url_for('payment_success', _external=True) + '?session_id={CHECKOUT_SESSION_ID}',
            cancel_url=url_for('payment_cancel', _external=True),
            customer_email=current_user.email,
            metadata={
                'user_id': current_user.user_id
            }
        )
        
        return redirect(checkout_session.url, code=303)
        
    except Exception as e:
        logger.error(f"Error creating checkout session: {e}")
        flash('Payment system temporarily unavailable. Please try again later.', 'error')
        return redirect(url_for('app_dashboard'))

@app.route('/payment-success')
@require_auth
def payment_success():
    """Handle successful payment"""
    session_id = request.args.get('session_id')
    
    if session_id:
        try:
            # Verify the session with Stripe
            checkout_session = stripe.checkout.Session.retrieve(session_id)
            
            if checkout_session.payment_status == 'paid':
                # Get current user
                session_token = session.get('session_token')
                current_user = AuthManager.get_user_from_session(session_token)
                
                if current_user:
                    # Update to pro plan
                    current_user.plan = 'pro'
                    current_user.trade_count = 0  # Reset count
                    
                    # Update session
                    session['user']['plan'] = 'pro'
                    session['user']['trade_count'] = 0
                    
                    flash('🎉 Welcome to Pro! You now have unlimited trades and advanced features!', 'success')
                    return redirect(url_for('app_dashboard'))
        
        except Exception as e:
            logger.error(f"Error verifying payment: {e}")
    
    flash('Payment verification failed. Please contact support if you were charged.', 'error')
    return redirect(url_for('app_dashboard'))

@app.route('/payment-cancel')
@require_auth
def payment_cancel():
    """Handle cancelled payment"""
    flash('Payment cancelled. You can upgrade anytime from your dashboard.', 'info')
    return redirect(url_for('app_dashboard'))

@app.errorhandler(404)
def not_found(error):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_error(error):
    return render_template('500.html'), 500

def get_league_from_cache():
    """Get full league data from cache"""
    if 'league_data' not in session:
        return None
    
    league_id = session['league_data'].get('league_id')
    if not league_id:
        return None
        
    if hasattr(dynasty_app, 'league_cache') and league_id in dynasty_app.league_cache:
        return dynasty_app.league_cache[league_id]
    
    # If not in cache, try to reconnect
    return dynasty_app.connect_league(league_id)

@app.context_processor
def inject_session_data():
    """Inject session data into all templates"""
    user_data = session.get('user')
    if user_data:
        logger.info(f"Template context - User: {user_data.get('email')}, Plan: {user_data.get('plan')}")
    return {
        'session': {
            'user': user_data,
            'league_data': session.get('league_data'),
            'selected_team': session.get('selected_team')
        }
    }

if __name__ == '__main__':
    # Create templates directory structure
    import os
    if not os.path.exists('templates'):
        os.makedirs('templates')
    
    # For development - in production use a proper WSGI server
    app.run(host='0.0.0.0', port=5000, debug=True)