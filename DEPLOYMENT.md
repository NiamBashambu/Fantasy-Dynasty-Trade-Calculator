# 🚀 Production Deployment Guide

## Overview
This guide covers deploying Dynasty Trade Analyzer to production with a PostgreSQL database and secure Stripe integration.

## Architecture
- **Frontend**: Netlify (static hosting)
- **Backend**: Railway (Flask app)
- **Database**: Railway PostgreSQL
- **Payments**: Stripe

## 🗄️ Database Setup (Railway)

### 1. Create Railway Account
1. Go to [railway.app](https://railway.app)
2. Sign up with GitHub
3. Create new project

### 2. Add PostgreSQL Database
1. Click "Add Service" → "Database" → "PostgreSQL"
2. Railway will automatically provision a database
3. Copy the `DATABASE_URL` from the database service

### 3. Initialize Database Schema
```bash
# Connect to your Railway database
psql $DATABASE_URL

# Run the schema file
\i database/schema.sql

# Verify tables were created
\dt
```

## 🚀 Backend Deployment (Railway)

### 1. Deploy Backend
1. In Railway, click "Add Service" → "GitHub Repository"
2. Connect your GitHub repository
3. Railway will auto-detect it's a Python app

### 2. Set Environment Variables
In Railway dashboard, go to your service → Variables:

```
SECRET_KEY=your-super-secret-production-key
FLASK_ENV=production
DATABASE_URL=postgresql://... (auto-provided by Railway)
OPENAI_API_KEY=sk-your-openai-key
STRIPE_SECRET_KEY=sk_live_your-stripe-secret
STRIPE_PUBLISHABLE_KEY=pk_live_your-stripe-publishable
STRIPE_ENDPOINT_SECRET=whsec_your-webhook-secret
```

### 3. Deploy Configuration
Railway will automatically:
- Install dependencies from `requirements.txt`
- Run `gunicorn` based on `railway.json`
- Provide a public URL

## 🌐 Frontend Deployment (Netlify)

### 1. Prepare Frontend
Update API endpoints in frontend files to point to your Railway backend URL.

### 2. Deploy to Netlify
1. Go to [netlify.com](https://netlify.com)
2. Connect GitHub repository
3. Set build settings:
   - Build command: `# No build needed`
   - Publish directory: `frontend`

### 3. Configure Redirects
Netlify will use the `netlify.toml` file automatically.

## 💳 Stripe Configuration

### 1. Stripe Dashboard Setup
1. Go to [stripe.com](https://stripe.com) → Dashboard
2. Create products for your subscription plans
3. Set up webhooks pointing to: `https://your-railway-app.railway.app/stripe-webhook`

### 2. Required Webhook Events
Enable these events in Stripe:
- `checkout.session.completed`
- `invoice.payment_succeeded`
- `invoice.payment_failed`
- `customer.subscription.updated`
- `customer.subscription.deleted`

### 3. Test Payments
Use Stripe test cards:
- Success: `4242 4242 4242 4242`
- Decline: `4000 0000 0000 0002`

## 🔐 Environment Variables Checklist

### Required for Production:
- [ ] `SECRET_KEY` - Flask secret (generate new one)
- [ ] `DATABASE_URL` - PostgreSQL connection string
- [ ] `OPENAI_API_KEY` - OpenAI API key
- [ ] `STRIPE_SECRET_KEY` - Stripe secret key (live)
- [ ] `STRIPE_PUBLISHABLE_KEY` - Stripe publishable key (live)
- [ ] `STRIPE_ENDPOINT_SECRET` - Webhook endpoint secret

### Optional:
- [ ] `FLASK_ENV=production`
- [ ] Custom database connection variables (if not using DATABASE_URL)

## 🧪 Testing Production Deployment

### 1. Health Check
```bash
curl https://your-railway-app.railway.app/health
```

### 2. Database Connection
Check Railway logs to ensure database connection is successful.

### 3. Stripe Integration
1. Test subscription upgrade flow
2. Verify webhook endpoint receives events
3. Check database for transaction records

## 📊 Monitoring

### Railway
- Check service logs in Railway dashboard
- Monitor database performance
- Set up alerts for downtime

### Stripe
- Monitor payment success rates
- Set up webhook monitoring
- Track subscription metrics

## 🔒 Security Checklist

- [ ] Use HTTPS only (enforced by Railway/Netlify)
- [ ] Secure session cookies configured
- [ ] Database credentials are environment variables
- [ ] Stripe webhook signatures verified
- [ ] Rate limiting implemented (if needed)
- [ ] SQL injection protection (parameterized queries)

## 🚨 Troubleshooting

### Database Connection Issues
```bash
# Test database connection
psql $DATABASE_URL -c "SELECT version();"
```

### Stripe Webhook Issues
1. Check webhook signature verification
2. Verify endpoint URL is accessible
3. Check Railway logs for webhook events

### Frontend API Issues
1. Verify CORS settings in Flask app
2. Check API endpoint URLs
3. Verify environment variables are set

## 📈 Scaling Considerations

### Database
- Railway PostgreSQL handles moderate traffic
- Consider connection pooling for high traffic
- Monitor query performance

### Backend
- Railway auto-scales within limits
- Consider Redis for session storage at scale
- Implement caching for API responses

### Frontend
- Netlify CDN handles global distribution
- Optimize images and assets
- Implement lazy loading

## 💰 Cost Estimates

### Railway (Backend + Database)
- Hobby Plan: $5/month
- Pro Plan: $20/month (recommended for production)

### Netlify (Frontend)
- Free tier: 100GB bandwidth
- Pro: $19/month for advanced features

### Stripe
- 2.9% + 30¢ per successful charge
- No monthly fees

**Total estimated cost: $25-40/month for production**
