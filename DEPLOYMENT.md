# Deployment

The app is a single stateless Flask service. There is no database, no object storage, and no background worker. Any host that can run a Python process works: Railway, Render, Fly.io, Heroku-style platforms, or a small VPS.

## What you need

- A host that runs `gunicorn wsgi:app`.
- Two environment variables in production:
  - `APP_ENV=production`
  - `SECRET_KEY` (generate one: `python -c "import secrets; print(secrets.token_hex(32))"`)
- Optional: `OPENAI_API_KEY` for AI-written explanations.
- Outbound HTTPS access to `api.sleeper.app`, `api.fantasycalc.com`, and (if used) `api.openai.com`.

The app refuses to start in production without `SECRET_KEY`.

## Railway

`railway.json` is already configured.

1. Push this repository to GitHub.
2. In Railway, create a project and choose **Deploy from GitHub repo**.
3. Add variables: `APP_ENV=production`, `SECRET_KEY`, and optionally `OPENAI_API_KEY`.
4. Deploy. Railway runs:
   ```
   gunicorn wsgi:app --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 60 --access-logfile -
   ```
   and checks `GET /health`.
5. Open the generated domain. HTTPS is provided by Railway.

Do not add a PostgreSQL service. Nothing uses it.

## Render, Fly.io, other hosts

Use these settings:

| Setting | Value |
|---------|-------|
| Build command | `pip install -r requirements.txt` |
| Start command | `gunicorn wsgi:app --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 60` |
| Health check path | `/health` |
| Python | 3.9 or newer |

## Running behind your own reverse proxy

Set `TRUST_PROXY=1` (on by default in production) so rate limiting sees the real client IP from `X-Forwarded-For`. Terminate HTTPS at the proxy. Session cookies are marked `Secure` in production, so the site must be served over HTTPS.

## Statelessness and scaling

- Caches (league data, trade values) and rate-limit counters live in each worker's memory. More workers or instances means a few more calls to Sleeper and FantasyCalc, never wrong results.
- Sessions are signed cookies, so any instance can serve any user. All instances must share the same `SECRET_KEY`.
- Trade values are also written to `CACHE_DIR` (default `./.cache`). On platforms with ephemeral disks this simply starts empty after each deploy. It is only a safety net if FantasyCalc is briefly down.
- Restarting or redeploying loses nothing important. Users stay connected because their league and team IDs are in the cookie.

## Smoke test after deploying

```bash
curl https://your-app.example.com/health
# {"status":"ok"}
```

Then in the browser:

1. Open the calculator, enter `Ja'Marr Chase` for Team A and `Bijan Robinson` for Team B, and submit.
2. Connect a real league ID and confirm your roster values appear on the dashboard.
3. Generate suggestions.

## Costs

- Hosting: a free or hobby tier is enough for light traffic.
- FantasyCalc and Sleeper: free public APIs, cached to keep request volume low.
- OpenAI (optional): only short explanations are generated, capped by `LLM_CALLS_PER_HOUR` per client. Leave `OPENAI_API_KEY` unset to spend nothing.

## Pre-launch checklist

- [ ] `APP_ENV=production` and a unique `SECRET_KEY` are set.
- [ ] The site is served over HTTPS.
- [ ] `/health` returns 200.
- [ ] FantasyCalc and Sleeper credits are visible in the footer.
- [ ] You have read FantasyCalc's terms of use if the site is commercial.
- [ ] A `LICENSE` file has been added.
