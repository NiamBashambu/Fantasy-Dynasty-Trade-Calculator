# Dynasty Trade Analyzer

A web application that suggests and evaluates dynasty fantasy football trades using league data from the Sleeper API and (optionally) OpenAI.

The project is designed to run **without a database**. All state is either fetched live from Sleeper, held in a short-lived in-process cache, stored in a signed cookie, or kept in the user's browser. See [PROJECT_GAPS.md](PROJECT_GAPS.md) for what is still missing and the plan to finish the project.

## Status

This is a working prototype, not a finished product. Read [PROJECT_GAPS.md](PROJECT_GAPS.md) before deploying it anywhere public. Notable current limitations:

- User accounts and sessions are held in process memory and are lost on every restart.
- Without an OpenAI key, trade suggestions and calculator results are hardcoded mock data.
- Payments (Stripe) are only partially wired and are not safe to enable.
- There are no automated tests.

## Features

- **Trade suggestions**: generate trade ideas for your team, tuned by strategy (contending, rebuilding, balanced), risk tolerance, and positional needs.
- **Trade calculator**: enter the players on each side of a trade and get a fairness score and written analysis.
- **Sleeper integration**: load a league by its Sleeper league ID and pick your team from the roster list.
- **Usage limits**: a free tier with a capped number of suggestions, to control OpenAI cost.
- **Responsive UI**: server-rendered Flask templates with a mobile-friendly layout.

## Quick Start

### Prerequisites

- Python 3.9 or higher
- A Sleeper league ID (the number in your league URL)
- An OpenAI API key (optional; without one the app returns mock responses)

### Installation

1. Clone the repository and enter it:
   ```bash
   git clone <your-repo-url>
   cd Fantasy-Dynasty-Trade-Calculator
   ```

2. Create a virtual environment and install dependencies:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

3. Set environment variables (copy `env.example` to `.env` or export them):
   ```bash
   export SECRET_KEY='a-long-random-string'
   export OPENAI_API_KEY='your-openai-api-key'   # optional
   ```

4. Run the app:
   ```bash
   python run.py
   ```

5. Open http://localhost:5000

## How It Works

1. **Connect a league**: enter your Sleeper league ID. The app fetches league info, users, and rosters from Sleeper.
2. **Select your team**: choose your team from the league's managers.
3. **Generate trades**: set your preferences and the app builds a prompt from the league data and asks OpenAI for suggestions.
4. **Evaluate a trade**: use the calculator to score any proposed trade.

## Data and State (No Database)

| Data | Where it lives |
|------|----------------|
| Sleeper league, rosters, users | Fetched live from Sleeper, cached in memory with a TTL |
| Sleeper NFL player list | Fetched from Sleeper, cached in memory (and optionally on disk) |
| Signed-in state, selected league and team | Signed Flask session cookie |
| Usage count / rate limit | In-memory counter keyed by client (resets on restart) |
| Trade history and saved preferences | Browser `localStorage` (planned) |
| Shared trades | Encoded in the share URL (planned) |

Because nothing is persisted server-side, the app can run on any host with a single process, and restarting it never loses anything the user cannot trivially recreate. If you run more than one worker, in-memory caches and counters are per-worker; this is acceptable for caches and is a known tradeoff for rate limits.

## Project Structure

```
Fantasy-Dynasty-Trade-Calculator/
├── backend/
│   ├── python_backend.py       # Flask app: routes, Sleeper client, OpenAI logic
│   └── database.py             # Unused PostgreSQL code (to be removed)
├── database/
│   └── schema.sql              # Unused PostgreSQL schema (to be removed)
├── frontend/
│   ├── index.html              # Landing page and sign-in form
│   └── dynasty_trade_analyzer.html   # Legacy standalone page (to be removed)
├── templates/                  # Jinja templates for the app
├── netlify/functions/app.py    # Experimental serverless wrapper (not working)
├── config.py                   # Config classes (not yet used by the app)
├── run.py                      # Local development entry point
├── requirements.txt
├── env.example
├── DEPLOYMENT.md               # Outdated; assumes PostgreSQL and Netlify + Railway
├── PROJECT_GAPS.md             # What is missing and what to do next
└── README.md
```

## Configuration

Environment variables:

| Variable | Required | Purpose |
|----------|----------|---------|
| `SECRET_KEY` | Yes (production) | Signs the session cookie. Use a long random value. |
| `OPENAI_API_KEY` | No | Enables real AI output. Without it the app returns mock data. |
| `FLASK_ENV` | No | `development` or `production`. |
| `STRIPE_*` | No | Reserved for a future paid tier. Not safe to enable yet. |

The Sleeper API is public and needs no key.

## Deployment

Run a single service with a production WSGI server:

```bash
gunicorn --bind 0.0.0.0:$PORT backend.python_backend:app
```

Any host that runs a Python process works (Railway, Render, Fly.io, a small VPS). No database service is needed. Serving the frontend from a separate static host is not recommended while the app relies on server-rendered pages and cookie sessions.

Before going live, complete the "Must fix before public release" list in [PROJECT_GAPS.md](PROJECT_GAPS.md). In particular: set a real `SECRET_KEY`, turn off debug mode, and serve over HTTPS.

## Troubleshooting

- **Module not found**: activate your virtual environment and run `pip install -r requirements.txt`.
- **Mock results instead of AI results**: `OPENAI_API_KEY` is not set, or the OpenAI call failed. Check the console logs.
- **Sleeper connection fails**: verify the league ID. Sleeper leagues are readable without authentication.
- **Signed out after restarting the server**: expected for now, since accounts are stored in memory.
- **Template not found**: run the app from the repository root with `python run.py`.

## License

No license has been chosen yet. Add a `LICENSE` file before sharing the code publicly.
