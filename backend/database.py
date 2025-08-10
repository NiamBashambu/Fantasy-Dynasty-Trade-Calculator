"""
Database connection and models for Dynasty Trade Analyzer
Production-ready PostgreSQL implementation
"""

import os
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.pool import SimpleConnectionPool
from contextlib import contextmanager
import logging
from typing import Optional, Dict, List, Any
from dataclasses import dataclass
from datetime import datetime, timedelta
import json

logger = logging.getLogger(__name__)

@dataclass
class User:
    id: Optional[int] = None
    email: str = ""
    password_hash: str = ""
    plan: str = "free"
    trade_count: int = 0
    stripe_customer_id: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

@dataclass
class UserSession:
    id: Optional[int] = None
    user_id: int = 0
    session_token: str = ""
    expires_at: datetime = None
    created_at: Optional[datetime] = None

@dataclass
class LeagueConnection:
    id: Optional[int] = None
    user_id: int = 0
    league_id: str = ""
    league_name: str = ""
    selected_team_id: Optional[str] = None
    selected_team_name: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

class DatabaseManager:
    """PostgreSQL database connection manager"""
    
    def __init__(self):
        self.pool = None
        self._initialize_pool()
    
    def _initialize_pool(self):
        """Initialize connection pool"""
        try:
            # Get database URL from environment
            database_url = os.environ.get('DATABASE_URL')
            
            if not database_url:
                # Fallback to individual components for development
                host = os.environ.get('DB_HOST', 'localhost')
                port = os.environ.get('DB_PORT', '5432')
                database = os.environ.get('DB_NAME', 'dynasty_trade_analyzer')
                user = os.environ.get('DB_USER', 'postgres')
                password = os.environ.get('DB_PASSWORD', '')
                
                database_url = f"postgresql://{user}:{password}@{host}:{port}/{database}"
            
            self.pool = SimpleConnectionPool(
                minconn=1,
                maxconn=20,
                dsn=database_url
            )
            logger.info("Database connection pool initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize database pool: {e}")
            raise
    
    @contextmanager
    def get_connection(self):
        """Get database connection from pool"""
        conn = None
        try:
            conn = self.pool.getconn()
            yield conn
        except Exception as e:
            if conn:
                conn.rollback()
            logger.error(f"Database operation failed: {e}")
            raise
        finally:
            if conn:
                self.pool.putconn(conn)
    
    def execute_query(self, query: str, params: tuple = None, fetch: bool = False):
        """Execute a database query"""
        with self.get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(query, params or ())
                if fetch:
                    return cur.fetchall()
                conn.commit()
                return cur.rowcount
    
    def execute_scalar(self, query: str, params: tuple = None):
        """Execute query and return single value"""
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, params or ())
                result = cur.fetchone()
                return result[0] if result else None

class UserManager:
    """User authentication and management"""
    
    def __init__(self, db: DatabaseManager):
        self.db = db
    
    def create_user(self, email: str, password_hash: str, plan: str = 'free') -> Optional[User]:
        """Create a new user"""
        try:
            query = """
                INSERT INTO users (email, password_hash, plan)
                VALUES (%s, %s, %s)
                RETURNING id, email, password_hash, plan, trade_count, stripe_customer_id, created_at, updated_at
            """
            result = self.db.execute_query(query, (email, password_hash, plan), fetch=True)
            
            if result:
                row = result[0]
                return User(
                    id=row['id'],
                    email=row['email'],
                    password_hash=row['password_hash'],
                    plan=row['plan'],
                    trade_count=row['trade_count'],
                    stripe_customer_id=row['stripe_customer_id'],
                    created_at=row['created_at'],
                    updated_at=row['updated_at']
                )
            return None
            
        except Exception as e:
            logger.error(f"Error creating user: {e}")
            return None
    
    def get_user_by_email(self, email: str) -> Optional[User]:
        """Get user by email"""
        try:
            query = """
                SELECT id, email, password_hash, plan, trade_count, stripe_customer_id, created_at, updated_at
                FROM users WHERE email = %s
            """
            result = self.db.execute_query(query, (email,), fetch=True)
            
            if result:
                row = result[0]
                return User(
                    id=row['id'],
                    email=row['email'],
                    password_hash=row['password_hash'],
                    plan=row['plan'],
                    trade_count=row['trade_count'],
                    stripe_customer_id=row['stripe_customer_id'],
                    created_at=row['created_at'],
                    updated_at=row['updated_at']
                )
            return None
            
        except Exception as e:
            logger.error(f"Error getting user by email: {e}")
            return None
    
    def get_user_by_id(self, user_id: int) -> Optional[User]:
        """Get user by ID"""
        try:
            query = """
                SELECT id, email, password_hash, plan, trade_count, stripe_customer_id, created_at, updated_at
                FROM users WHERE id = %s
            """
            result = self.db.execute_query(query, (user_id,), fetch=True)
            
            if result:
                row = result[0]
                return User(
                    id=row['id'],
                    email=row['email'],
                    password_hash=row['password_hash'],
                    plan=row['plan'],
                    trade_count=row['trade_count'],
                    stripe_customer_id=row['stripe_customer_id'],
                    created_at=row['created_at'],
                    updated_at=row['updated_at']
                )
            return None
            
        except Exception as e:
            logger.error(f"Error getting user by ID: {e}")
            return None
    
    def update_user_plan(self, user_id: int, plan: str, reset_trade_count: bool = True) -> bool:
        """Update user's subscription plan"""
        try:
            if reset_trade_count:
                query = """
                    UPDATE users SET plan = %s, trade_count = 0, updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """
                self.db.execute_query(query, (plan, user_id))
            else:
                query = """
                    UPDATE users SET plan = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """
                self.db.execute_query(query, (plan, user_id))
            return True
            
        except Exception as e:
            logger.error(f"Error updating user plan: {e}")
            return False
    
    def increment_trade_count(self, user_id: int) -> bool:
        """Increment user's trade count"""
        try:
            query = """
                UPDATE users SET trade_count = trade_count + 1, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
            """
            self.db.execute_query(query, (user_id,))
            return True
            
        except Exception as e:
            logger.error(f"Error incrementing trade count: {e}")
            return False
    
    def update_stripe_customer_id(self, user_id: int, stripe_customer_id: str) -> bool:
        """Update user's Stripe customer ID"""
        try:
            query = """
                UPDATE users SET stripe_customer_id = %s, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
            """
            self.db.execute_query(query, (stripe_customer_id, user_id))
            return True
            
        except Exception as e:
            logger.error(f"Error updating Stripe customer ID: {e}")
            return False

class SessionManager:
    """User session management"""
    
    def __init__(self, db: DatabaseManager):
        self.db = db
    
    def create_session(self, user_id: int, session_token: str, duration_hours: int = 24) -> bool:
        """Create a new session"""
        try:
            expires_at = datetime.utcnow() + timedelta(hours=duration_hours)
            query = """
                INSERT INTO user_sessions (user_id, session_token, expires_at)
                VALUES (%s, %s, %s)
            """
            self.db.execute_query(query, (user_id, session_token, expires_at))
            return True
            
        except Exception as e:
            logger.error(f"Error creating session: {e}")
            return False
    
    def get_user_from_session(self, session_token: str) -> Optional[User]:
        """Get user from session token"""
        try:
            query = """
                SELECT u.id, u.email, u.password_hash, u.plan, u.trade_count, 
                       u.stripe_customer_id, u.created_at, u.updated_at
                FROM users u
                JOIN user_sessions s ON u.id = s.user_id
                WHERE s.session_token = %s AND s.expires_at > CURRENT_TIMESTAMP
            """
            result = self.db.execute_query(query, (session_token,), fetch=True)
            
            if result:
                row = result[0]
                return User(
                    id=row['id'],
                    email=row['email'],
                    password_hash=row['password_hash'],
                    plan=row['plan'],
                    trade_count=row['trade_count'],
                    stripe_customer_id=row['stripe_customer_id'],
                    created_at=row['created_at'],
                    updated_at=row['updated_at']
                )
            return None
            
        except Exception as e:
            logger.error(f"Error getting user from session: {e}")
            return None
    
    def delete_session(self, session_token: str) -> bool:
        """Delete a session (logout)"""
        try:
            query = "DELETE FROM user_sessions WHERE session_token = %s"
            self.db.execute_query(query, (session_token,))
            return True
            
        except Exception as e:
            logger.error(f"Error deleting session: {e}")
            return False
    
    def cleanup_expired_sessions(self) -> int:
        """Clean up expired sessions"""
        try:
            query = "DELETE FROM user_sessions WHERE expires_at <= CURRENT_TIMESTAMP"
            return self.db.execute_query(query)
            
        except Exception as e:
            logger.error(f"Error cleaning up sessions: {e}")
            return 0

class LeagueManager:
    """League connection management"""
    
    def __init__(self, db: DatabaseManager):
        self.db = db
    
    def save_league_connection(self, user_id: int, league_id: str, league_name: str,
                             selected_team_id: str = None, selected_team_name: str = None) -> bool:
        """Save or update league connection"""
        try:
            query = """
                INSERT INTO league_connections (user_id, league_id, league_name, selected_team_id, selected_team_name)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (user_id, league_id)
                DO UPDATE SET 
                    league_name = EXCLUDED.league_name,
                    selected_team_id = EXCLUDED.selected_team_id,
                    selected_team_name = EXCLUDED.selected_team_name,
                    updated_at = CURRENT_TIMESTAMP
            """
            self.db.execute_query(query, (user_id, league_id, league_name, selected_team_id, selected_team_name))
            return True
            
        except Exception as e:
            logger.error(f"Error saving league connection: {e}")
            return False
    
    def get_user_league(self, user_id: int, league_id: str) -> Optional[LeagueConnection]:
        """Get user's league connection"""
        try:
            query = """
                SELECT id, user_id, league_id, league_name, selected_team_id, selected_team_name, created_at, updated_at
                FROM league_connections 
                WHERE user_id = %s AND league_id = %s
            """
            result = self.db.execute_query(query, (user_id, league_id), fetch=True)
            
            if result:
                row = result[0]
                return LeagueConnection(
                    id=row['id'],
                    user_id=row['user_id'],
                    league_id=row['league_id'],
                    league_name=row['league_name'],
                    selected_team_id=row['selected_team_id'],
                    selected_team_name=row['selected_team_name'],
                    created_at=row['created_at'],
                    updated_at=row['updated_at']
                )
            return None
            
        except Exception as e:
            logger.error(f"Error getting league connection: {e}")
            return None

class StripeManager:
    """Stripe transaction management"""
    
    def __init__(self, db: DatabaseManager):
        self.db = db
    
    def save_transaction(self, user_id: int, stripe_session_id: str, amount: int,
                        plan_type: str, status: str = 'pending') -> bool:
        """Save Stripe transaction"""
        try:
            query = """
                INSERT INTO stripe_transactions (user_id, stripe_session_id, amount, plan_type, status)
                VALUES (%s, %s, %s, %s, %s)
            """
            self.db.execute_query(query, (user_id, stripe_session_id, amount, plan_type, status))
            return True
            
        except Exception as e:
            logger.error(f"Error saving Stripe transaction: {e}")
            return False
    
    def update_transaction_status(self, stripe_session_id: str, status: str, 
                                 stripe_payment_intent_id: str = None) -> bool:
        """Update transaction status"""
        try:
            if stripe_payment_intent_id:
                query = """
                    UPDATE stripe_transactions 
                    SET status = %s, stripe_payment_intent_id = %s
                    WHERE stripe_session_id = %s
                """
                self.db.execute_query(query, (status, stripe_payment_intent_id, stripe_session_id))
            else:
                query = """
                    UPDATE stripe_transactions 
                    SET status = %s
                    WHERE stripe_session_id = %s
                """
                self.db.execute_query(query, (status, stripe_session_id))
            return True
            
        except Exception as e:
            logger.error(f"Error updating transaction status: {e}")
            return False

# Global database instances
db_manager = None
user_manager = None
session_manager = None
league_manager = None
stripe_manager = None

def initialize_database():
    """Initialize database managers"""
    global db_manager, user_manager, session_manager, league_manager, stripe_manager
    
    try:
        db_manager = DatabaseManager()
        user_manager = UserManager(db_manager)
        session_manager = SessionManager(db_manager)
        league_manager = LeagueManager(db_manager)
        stripe_manager = StripeManager(db_manager)
        
        logger.info("Database managers initialized successfully")
        return True
        
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        return False

def get_database_managers():
    """Get all database managers"""
    return {
        'db': db_manager,
        'user': user_manager,
        'session': session_manager,
        'league': league_manager,
        'stripe': stripe_manager
    }
