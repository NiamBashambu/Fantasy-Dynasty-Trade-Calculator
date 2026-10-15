# Project Gaps and Roadmap

This document lists what is missing or broken in the current codebase and what to do to turn it into a real, deployable project **without a database**.

It is based on a read-through of `backend/python_backend.py`, `config.py`, `run.py`, the templates, and the deployment files.

## 1. Where the project is today

The Flask app has a working shape: landing page, sign-up/sign-in, Sleeper league connection, team selection, a trade generator, and a trade calculator. But:

- Accounts and sessions live in two Python dicts (`users_db`, `sessions_db`), so every restart deletes all users.
- The PostgreSQL layer (`backend/database.py`, `database/schema.sql`) is never imported by the app. It is dead code, except that `netlify/functions/app.py` imports it.
- The "AI" has no ground-truth player values. It asks GPT-4 to guess values from player names, so results are not trustworthy.
- Without an OpenAI key, the app silently returns hardcoded mock trades (named real players, fixed 2024/2025 picks) and a calculator that scores trades by player count.
- Stripe checkout exists but is incomplete and the plan system can be bypassed.
- There are no tests, no CI, and the deployment configuration does not match how the app actually works.

## 2. Decision: how to do this without a database

The database was only ever needed for accounts, plans, payments, and history. Most of that can be removed.

### Recommended: drop accounts, make it stateless

| Need | Database-free approach |
|------|------------------------|
| Identify the user | No login. The user enters their Sleeper username or league ID. Store the choice in a signed cookie and/or browser `localStorage`. |
| Free/paid limits | Replace per-account plans with a per-client rate limit (for example Flask-Limiter with in-memory storage, keyed by IP plus cookie). Its purpose is to protect your OpenAI bill. |
| League and roster data | Fetch from Sleeper on demand; cache in memory with a TTL (for example `cachetools.TTLCache`). |
| Player database (about 5 MB from Sleeper) | In-memory cache refreshed at most once a day. Optionally write it to a JSON file on disk so restarts do not refetch it. |
| Player/pick values | Fetch from a public values source and cache it (see section 4). |
| Trade history | Save in the browser (`localStorage`) and offer JSON export. |
| Sharing a trade | Encode the trade into a URL (base64 or a signed token via `itsdangerous`), so no server storage is needed. |

### If you still want paid features later

Use Stripe as the source of truth instead of your own table:

- Sign-in by emailed magic link, where the link is a signed, time-limited token (`itsdangerous`). No stored password or user table.
- On sign-in, look up the Stripe customer by email and check for an active subscription. Put the result in the signed session cookie.
- Use the Stripe Customer Portal for cancellation and billing changes.
- Handle `/stripe-webhook` only for cache invalidation or logging, not for state you cannot recover.

Do this only after the free, stateless version is solid. The simplest honest v1 is: free, no login, rate limited.

## 3. Must fix before any public release

These are correctness or security problems in the current code.

1. **Anyone can upgrade to Pro for free.** `/update-plan` (`python_backend.py`) sets the plan from a form field with no payment check. Remove this route and the dashboard form that posts to it.
2. **Debug mode is on.** `run.py` and the `__main__` block call `app.run(host='0.0.0.0', debug=True)`. The Werkzeug debugger allows remote code execution if exposed. Bind to `127.0.0.1` locally and never enable debug in production.
3. **Weak secret key fallbacks.** `SECRET_KEY` falls back to a hardcoded string in both `python_backend.py` and `run.py`. In production, fail to start if it is unset.
4. **No CSRF protection.** The README previously claimed CSRF protection; the app has none. All forms post without tokens. Add Flask-WTF `CSRFProtect` (or equivalent).
5. **Session cookie flags are not applied.** `config.py` defines `SESSION_COOKIE_SECURE`, `HTTPONLY`, and `SAMESITE`, but the app never loads `config.py`. Set them on `app.config`.
6. **Reflected message on sign-in errors.** The sign-in failure redirects to `/?error=1&message=...`. Confirm `frontend/index.html` does not inject that query value into the page as HTML, and prefer flash messages.
7. **User enumeration.** Sign-in returns different messages for "wrong password" and "no such account". Moot if accounts are removed.
8. **Unbounded and unvalidated input.** The league ID, player lists, and `additional_notes` go straight into URLs and LLM prompts. Validate the league ID (digits only), cap list sizes and text length, and treat user text as untrusted in prompts (prompt injection).
9. **No timeouts on outbound requests.** Every `requests.get` to Sleeper has no `timeout=`, so one slow response can hang a worker. OpenAI calls need a timeout too.
10. **Healthcheck points to a missing route.** `railway.json` uses `/health`, which does not exist, so deploys will fail their healthcheck. Add a `/health` route.
11. **Sensitive logging.** Emails and plan data are logged at INFO on every template render (`inject_session_data`). Remove it.
12. **Committed build artifact.** `backend/__pycache__/python_backend.cpython-311.pyc` is tracked in git. Remove it and extend `.gitignore` (see section 7).

## 4. Product gaps (what makes it a real trade tool)

1. **A real value source.** The core of a trade calculator is a consistent value for every player and pick. Today there is none. Pull values from a public dynasty values source (for example FantasyCalc's public API or KeepTradeCut data; check each one's terms of use), cache them, and compute fairness deterministically in Python. Use the LLM only to explain the result in words, not to produce the numbers.
2. **Draft picks.** Sleeper exposes traded picks per league (`/league/{id}/traded_picks`). The app never reads them, yet the mock and AI output mention picks. Add pick ownership and pick values.
3. **League settings awareness.** Scoring format (PPR, superflex, TE premium), roster slots, and number of teams change values significantly. Read them from the Sleeper league object and adjust values.
4. **Validated suggestions.** The AI can invent players or suggest a player the partner does not own. After generation, verify each named player exists in the Sleeper player list and is on the correct roster; drop or regenerate invalid trades.
5. **Better prompt context.** The prompt currently includes only 15 of your players and 5 teams with 8 players each. Build it from roster needs, team strategy (contender or rebuilder, inferred from standings and age), and the value data.
6. **Structured model output.** `json.loads(response.content)` breaks whenever the model adds text. Use JSON mode or structured outputs, validate with a schema (for example Pydantic), and retry once on failure.
7. **Player search in the calculator.** The calculator takes free text, one name per line, and never resolves it. Add autocomplete backed by the Sleeper player list so names map to player IDs.
8. **Honest fallback behavior.** Mock output should be clearly labeled as a demo, or removed. Showing hardcoded trades for a user's league as if they were real is misleading. The mock strategy filter also matches substrings like "young", which is fragile.
9. **Implement or remove the stubs.** "Share Trade" and "Refine" are placeholders that only flash "coming soon". Build them (see section 2 for database-free sharing) or hide the buttons.
10. **Team selection.** The user picks any manager in the league with no verification. Either accept that (it is read-only data) or let the user enter their Sleeper username and match the roster automatically.
11. **Current season handling.** Defaults like `season='2025'` and mock picks for 2024/2025 are stale. Derive the season from Sleeper's state endpoint (`/state/nfl`).

## 5. Code and architecture gaps

1. **One 1,000-line file.** Split `python_backend.py` into modules: `sleeper.py` (client and caching), `values.py`, `analyzer.py` (LLM), `routes/` (blueprints), and an app factory (`create_app`).
2. **Config is duplicated.** `config.py` is unused; the app reads environment variables directly in several places. Load one config object in the app factory.
3. **Stateful hacks.** `dynasty_app.league_cache` is created with `hasattr` inside a route, is unbounded, and is per-process. Replace it with a TTL cache.
4. **Player cache bug.** `(now - self.cache_timestamp).seconds > 3600` uses `.seconds`, which wraps every 24 hours. Use `.total_seconds()`.
5. **Multi-worker breakage.** In-memory users and sessions cannot work under gunicorn with more than one worker, because each worker has its own copy. Another reason to go stateless (signed cookies work across workers).
6. **Dead code.** Remove `backend/database.py`, `database/schema.sql`, `psycopg2-binary`, and `SQLAlchemy` from `requirements.txt`, plus the DB variables in `env.example` and `config.py`.
7. **Duplicate frontends.** `frontend/index.html` and `frontend/dynasty_trade_analyzer.html` both exist, and the latter is described as legacy. Delete the legacy file. Move the landing page into `templates/` so there is one rendering path.
8. **Old dependencies.** `openai==1.3.5` is very old and the model is hardcoded to `gpt-4`. Upgrade the SDK and make the model a config value.
9. **Dead imports and style.** Unused imports (`hashlib`, `Player`, `User` fields such as `last_login`), a hand-written `require_auth` decorator without `functools.wraps`, and inline CSS repeated across templates.
10. **README setup script.** The old README referenced `setup.sh`, which does not exist. A `Makefile` or documented commands would replace it.

## 6. Deployment gaps

1. **Pick one host.** The repo is set up for both Netlify (static frontend plus a function) and Railway (Flask). They do not work together as configured.
   - `netlify/functions/app.py` is not a valid WSGI adapter: it always returns status 200 and `text/html`, ignores the real status and headers, mishandles the request body, and calls the unused DB initializer.
   - `netlify.toml` rewrites `/*` to `index.html`, which breaks all server-rendered routes.
   - The sign-in form posts to `/auth`, which a static Netlify site cannot serve.
2. **Recommended.** Deploy the Flask app as a single service (Railway, Render, Fly.io) with gunicorn. Delete `netlify.toml` and `netlify/`.
3. **Rewrite `DEPLOYMENT.md`.** It currently covers PostgreSQL setup, the DB schema, database health checks, and a `/stripe-webhook` route that does not exist. It also contains emojis. Replace it with a short guide for the single-service setup.
4. **Gunicorn settings.** Specify workers, a request timeout longer than your OpenAI timeout, and logging to stdout.
5. **HTTPS and headers.** Enforce HTTPS at the host, and add security headers (for example Flask-Talisman).
6. **Reverse-proxy awareness.** If behind a proxy, use `ProxyFix` so rate limiting sees real client IPs.

## 7. Repository hygiene

- Expand `.gitignore`: `__pycache__/`, `*.pyc`, `.venv/`, `.env`, `.pytest_cache/`, cached data files.
- Rename the project folder references in the README and docs consistently (older docs mention "Dynasty trade calculator").
- Add a `LICENSE`.
- Add a `pyproject.toml` or pin dev tools (ruff, pytest).
- Commit messages such as "production ready now" were premature; the app is not production ready yet.
- If payments are ever added, add Terms of Service and a Privacy Policy.

## 8. Testing and quality

Nothing is tested today. Add, in this order:

1. Unit tests for fairness math once values are deterministic.
2. Tests for the Sleeper client using recorded fixtures (`responses` or `pytest-httpx`), not live calls.
3. Tests for LLM output parsing with canned good and malformed responses.
4. Flask route tests using the test client (validation errors, rate limiting, CSRF).
5. A GitHub Actions workflow that runs lint and tests on every push.

## 9. Suggested order of work

**Phase 1: Make it safe and honest (about 1 to 2 days)**
- Remove `/update-plan`, debug mode, and secret fallbacks.
- Add CSRF protection, cookie flags, request timeouts, and `/health`.
- Delete dead database code and unused dependencies; clean `.gitignore`.

**Phase 2: Go stateless (about 2 to 3 days)**
- Remove sign-up and sign-in; identify users by Sleeper league ID and team.
- Replace plans with a rate limiter.
- Add TTL caches for Sleeper data; fix the player cache bug.
- Move the landing page into templates and delete the legacy HTML.

**Phase 3: Make results real (about 1 week)**
- Integrate a values source and compute fairness in code.
- Add draft picks and league scoring awareness.
- Switch the LLM to structured output, validate suggestions against rosters, and use the LLM for explanation only.
- Add player autocomplete in the calculator.

**Phase 4: Finish features (about 3 to 5 days)**
- Browser-side trade history with export.
- URL-based trade sharing.
- Implement or remove "Refine".

**Phase 5: Ship (about 2 to 3 days)**
- Add tests and CI.
- Consolidate on one host; rewrite `DEPLOYMENT.md`.
- Add a `LICENSE` and a short privacy note (what is sent to OpenAI and Sleeper).

## 10. Definition of done for v1

- Starts from a clean clone with documented commands and no external services except Sleeper (and OpenAI if enabled).
- No database, no persisted server state, and a restart loses nothing important.
- Trade values come from real data; the LLM only explains.
- Suggestions are validated against real rosters.
- No route allows privilege escalation; CSRF and cookie settings are on; debug is off.
- Tests and CI pass.
- Deploys as one service with a working healthcheck.
