# Project Status and Remaining Work

This replaces the earlier gap analysis. The first section records what was fixed; the rest lists what is still open.

## Fixed in the rewrite

| Problem | Resolution |
|---------|------------|
| Anyone could upgrade to Pro for free via `/update-plan` | Accounts, plans, and Stripe were removed entirely. The app is stateless. |
| Accounts and sessions lost on every restart; broke with multiple workers | No accounts. Selected league and team live in a signed cookie. |
| Dead PostgreSQL layer (`database.py`, `schema.sql`, `psycopg2`, `SQLAlchemy`) | Deleted. |
| LLM guessed trade values; mock trades returned fake data as if real | Values come from FantasyCalc. Suggestions are found by search over real rosters. The LLM only writes explanations and is optional. Mock data is gone. |
| Calculator scored trades by player count when no OpenAI key | Real market values, name and pick matching, and clear errors for unknown names. |
| No draft pick support | Pick values (2027 to 2029) and pick ownership including traded picks from Sleeper. |
| League format ignored | Values adjusted for Superflex vs 1 QB, PPR, and league size. |
| Debug mode on with `host=0.0.0.0` | Local server binds to `127.0.0.1`; debug is opt-in and never on in production. |
| Hardcoded `SECRET_KEY` fallbacks | Production refuses to start without one; development uses a random temporary key. |
| No CSRF protection | Flask-WTF CSRF on every form. |
| Cookie flags unused (`config.py` never loaded) | `Secure`, `HttpOnly`, `SameSite=Lax` configured; `Secure` on in production. |
| Reflected message in sign-in redirect URL | Sign-in removed; messages use flash and are auto-escaped. |
| No timeouts on Sleeper/OpenAI calls | Timeouts on every outbound request; Sleeper, values, and LLM failures degrade gracefully. |
| `.seconds` player cache bug | The 5 MB Sleeper player dump is no longer fetched; values carry the names and IDs. |
| Unbounded, per-process league cache built with `hasattr` | Bounded TTL cache. |
| Unvalidated input into URLs and prompts | League ID and username validated; calculator input capped; free-text notes removed so nothing user-written reaches the prompt. |
| Healthcheck pointed at a missing `/health` route | Route added. |
| Broken Netlify function and conflicting Netlify + Railway setup | Removed. Single-service deployment. |
| `.pyc` committed, thin `.gitignore` | Removed and expanded. |
| Stub features ("Share", "Refine") | Removed rather than left as dead links. |
| Stale 2024/2025 hardcoded seasons | Seasons come from Sleeper and from FantasyCalc's pick list. |
| Emojis and a database-based README and `DEPLOYMENT.md` | Rewritten. |
| No tests | 43 tests with no network access, covering values, matching, fairness math, trade search, routes, CSRF, rate limits, LLM fallback, and config. |

## Still open

### Product

1. **Verify against a real league.** Development tested against live FantasyCalc and Sleeper endpoints and a simulated 12-team league built from real player IDs. Run it once against your own league and compare the roster values to what you expect.
2. **Trade tuning.** The depth discount (`DEPTH_DECAY = 0.92`), the minimum asset value for the search (`MIN_TRADE_ASSET_VALUE = 800`), and the strategy filters are reasonable heuristics, not validated against historical trades. Adjust them after using the app on real leagues.
3. **Partner perspective.** Suggestions are fair by value but do not check whether the other manager needs what you are sending. Add a roster-need model (starters by position) to rank partners.
4. **Player autocomplete in the calculator.** Today names are matched on submit with "did you mean" hints. Autocomplete needs a small JS file served from `static/` (the content security policy blocks inline scripts).
5. **Trade history and sharing.** Both can stay database-free: history in browser `localStorage`, shared trades encoded in a URL or signed token.
6. **Custom scoring.** TE premium and other scoring tweaks are not reflected in values.
7. **Deep bench players** have no market value and are ignored.

### Engineering

1. **CI.** Add a GitHub Actions workflow that installs `requirements-dev.txt` and runs `pytest` (a ready-to-use workflow is in `.github/workflows/tests.yml`).
2. **Linting and typing.** Add `ruff` and optionally `mypy`.
3. **Observability.** Logging is plain text to stdout. Add request IDs or a hosted log drain if you deploy publicly.
4. **Rate limiting across instances.** Limits are per process. If you scale beyond a couple of instances and need strict limits, put them at the proxy or CDN instead of adding a database.
5. **FantasyCalc dependency.** If FantasyCalc changes its API or blocks you, values stop refreshing (cached values keep working). A second source such as KeepTradeCut would be a fallback; check its terms first.
6. **Dependency updates.** Dependencies are version-ranged, not locked. Add a lock file (`pip-tools` or `uv`) before relying on reproducible builds.

### Legal and housekeeping

- Add a `LICENSE`.
- Read FantasyCalc's and Sleeper's API terms before any commercial use. If you ever add payments or accounts again, add Terms of Service and a Privacy Policy.
- A short privacy note should say that league IDs go to Sleeper and, if enabled, trade details go to OpenAI.
