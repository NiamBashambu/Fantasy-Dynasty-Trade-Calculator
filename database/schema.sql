-- Dynasty Trade Analyzer Database Schema
-- PostgreSQL Database Setup

-- Users table with authentication
CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    plan VARCHAR(20) DEFAULT 'free' CHECK (plan IN ('free', 'pro')),
    trade_count INTEGER DEFAULT 0,
    stripe_customer_id VARCHAR(100),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- User sessions table
CREATE TABLE user_sessions (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    session_token VARCHAR(255) UNIQUE NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- League connections
CREATE TABLE league_connections (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    league_id VARCHAR(100) NOT NULL,
    league_name VARCHAR(255),
    selected_team_id VARCHAR(100),
    selected_team_name VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, league_id)
);

-- Stripe transactions
CREATE TABLE stripe_transactions (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    stripe_session_id VARCHAR(255),
    stripe_payment_intent_id VARCHAR(255),
    amount INTEGER, -- in cents
    currency VARCHAR(3) DEFAULT 'usd',
    status VARCHAR(50),
    plan_type VARCHAR(20),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Trade history
CREATE TABLE trade_history (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    league_id VARCHAR(100),
    trade_data JSONB, -- Store the full trade suggestion
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for performance
CREATE INDEX idx_users_email ON users(email);
CREATE INDEX idx_sessions_token ON user_sessions(session_token);
CREATE INDEX idx_sessions_user_id ON user_sessions(user_id);
CREATE INDEX idx_league_connections_user_id ON league_connections(user_id);
CREATE INDEX idx_stripe_transactions_user_id ON stripe_transactions(user_id);
CREATE INDEX idx_trade_history_user_id ON trade_history(user_id);

-- Update timestamp trigger
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_users_updated_at BEFORE UPDATE ON users 
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_league_connections_updated_at BEFORE UPDATE ON league_connections 
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
