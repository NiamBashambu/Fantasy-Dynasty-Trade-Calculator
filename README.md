# Dynasty Trade Analyzer

A small web app that prices dynasty fantasy football trades with market data and finds balanced trades in your Sleeper league.

There is **no database and no user accounts**. League data comes live from the Sleeper API, trade values come from the public FantasyCalc API, and everything is cached in memory (with a JSON file on disk as a safety net). Restarting the app loses nothing important.

## What it does

- **Trade calculator**: enter players and draft picks for each side (for example `Ja'Marr Chase` and `2027 1st`) and get a fairness score, a verdict, and assets that could balance the deal. Works without a league.
- **Trade suggestions**: connect a Sleeper league, pick your team, choose a strategy (contending, rebuilding, balanced), how even trades must be, and positions you need. The app searches every roster for 1-for-1, 2-for-1, and 1-for-2 trades that fit.
- **League aware**: values are adjusted for Superflex vs 1 QB, PPR scoring, and league size. Draft pick ownership (including traded picks) comes from Sleeper.
- **Optional AI explanations**: if you set `OPENAI_API_KEY`, a model writes short explanations of results. It never sets values, picks players, or scores fairness. Without a key, built-in text is used.

## How trade values work

Values come from [FantasyCalc](https://www.fantasycalc.com), whose numbers are derived from real dynasty trades and updated regularly. For every asset the app has:

- a dynasty value for the league format,
- a redraft value (used to judge "win now" fit),
- age, position, team, and rank.

Fairness is computed in plain Python:

1. For each side, sort assets by value and apply a small depth discount (each extra asset counts 92% of the previous one) because two mid-tier assets rarely equal one star. This constant is a tunable heuristic, not a market number. It lives in `dynasty/trades.py`.
2. Fairness is `100 * smaller_total / larger_total`. 90 or higher reads as an even trade.
3. The side that **receives** the larger package wins.

Suggestions are found by search, then filtered by your strategy:

- **Contending** requires more win-now (redraft) value coming back than going out.
- **Rebuilding** requires a younger value-weighted age coming back, or draft capital.
- **Balanced** scores both.
- Stated position needs add to a trade's score. Each trade is limited to one reuse of any partner asset and two uses of any of yours, with at most two trades per partner.

### Why not train a model?

A model needs labeled data, meaning a ground-truth answer for "what is this player worth". That data is the private trade history that FantasyCalc and KeepTradeCut already aggregate and sell/publish as values. Training your own on public data would mean guessing values from stats, which is a worse, harder-to-maintain version of a free API. This project uses the market values directly and keeps the logic transparent and testable.

Limitations of the current approach:

- No TE-premium or custom scoring adjustments beyond PPR and Superflex.
- Values reflect the market, not your league's specific mood or needs.
- Only players and picks FantasyCalc prices (roughly the top 400 plus picks 2027 to 2029) are considered; deep bench players are ignored.
- Check FantasyCalc's terms before using this commercially, and keep the credit in the footer.

## Quick start

Requires Python 3.9 or newer.

```bash
git clone <your-repo-url>
cd Fantasy-Dynasty-Trade-Calculator
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
python run.py
```

Open http://127.0.0.1:5000.

Optional environment variables (see `env.example`):

```bash
export OPENAI_API_KEY='sk-...'   # AI-written explanations
export SECRET_KEY='...'          # required when APP_ENV=production
```

Run the tests:

```bash
pytest
```

## Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `APP_ENV` | `development` | `production` requires `SECRET_KEY`, enables secure cookies, trusts proxy headers |
| `SECRET_KEY` | random per process in dev | Signs the session cookie |
| `OPENAI_API_KEY` | unset | Enables AI explanations |
| `OPENAI_MODEL` | `gpt-4o-mini` | Model used for explanations |
| `LLM_CALLS_PER_HOUR` | `20` | Per-client cap on AI calls (falls back to built-in text) |
| `REQUESTS_PER_MINUTE` | `60` | Per-client cap on form submissions |
| `VALUES_CACHE_SECONDS` | `21600` | How long FantasyCalc values are cached in memory |
| `LEAGUE_CACHE_SECONDS` | `300` | How long Sleeper league data is cached |
| `CACHE_DIR` | `./.cache` | Where the on-disk copy of values is kept (safe to delete) |
| `TRUST_PROXY` | on in production | Use `X-Forwarded-*` headers so rate limits see real client IPs |

## Where state lives

| Data | Location |
|------|----------|
| League, rosters, traded picks | Fetched from Sleeper, cached in memory for 5 minutes |
| Trade values | Fetched from FantasyCalc, cached in memory for 6 hours, mirrored to `.cache/*.json` |
| Selected league and team | Signed session cookie (IDs and display names only) |
| Rate limit counters | In memory, per process |

With more than one worker the caches and counters are per worker. That is fine for caches, and it means rate limits are approximate.

## Project layout

```
dynasty/
  __init__.py      app factory, security headers, error handlers, CSRF
  config.py        environment-based configuration
  sleeper.py       Sleeper API client and league model
  values.py        FantasyCalc client, name and pick matching, disk cache
  trades.py        fairness math and trade search
  llm.py           optional OpenAI explanations (never sets values)
  cache.py         TTL cache and rate limiter
  routes.py        pages
  templates/       Jinja templates
  static/          CSS
tests/             pytest suite (no network needed)
wsgi.py            production entry point (gunicorn wsgi:app)
run.py             local development entry point
```

## Deployment

Deploy as a single web service. No database or other service is needed. See [DEPLOYMENT.md](DEPLOYMENT.md).

## Troubleshooting

- **"Trade values are temporarily unavailable"**: FantasyCalc could not be reached and no cached copy exists yet. Try again shortly; once fetched, values survive outages.
- **"No Sleeper league was found"**: use the numeric ID from the league URL on Sleeper, not the league name.
- **A player is not found in the calculator**: names must match FantasyCalc spellings (punctuation, accents, and Jr/Sr suffixes are ignored). Picks use `2027 1st`; only 2027 to 2029 are priced.
- **Everything resets after a restart in development**: without `SECRET_KEY` a new key is generated each run, which signs you out. Set one to stay signed in.

## Remaining work

See [PROJECT_GAPS.md](PROJECT_GAPS.md).
