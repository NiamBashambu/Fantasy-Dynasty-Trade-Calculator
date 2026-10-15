"""Dynasty Trade Analyzer: a database-free Flask app."""

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from flask import Flask, render_template, request
from flask_wtf.csrf import CSRFError, CSRFProtect
from werkzeug.middleware.proxy_fix import ProxyFix

from .cache import SlidingWindowLimiter
from .config import load_config
from .llm import Explainer
from .sleeper import SleeperClient
from .values import ValuesClient

csrf = CSRFProtect()


@dataclass
class Services:
    sleeper: SleeperClient
    values: ValuesClient
    explainer: Optional[Explainer]
    llm_limiter: SlidingWindowLimiter
    request_limiter: SlidingWindowLimiter


def build_services(config: Dict[str, Any]) -> Services:
    explainer = None
    if config.get("OPENAI_API_KEY"):
        explainer = Explainer(
            api_key=config["OPENAI_API_KEY"],
            model=config["OPENAI_MODEL"],
            timeout=config["OPENAI_TIMEOUT"],
        )
    return Services(
        sleeper=SleeperClient(
            base_url=config["SLEEPER_BASE_URL"],
            timeout=config["SLEEPER_TIMEOUT"],
            cache_ttl=config["LEAGUE_CACHE_SECONDS"],
        ),
        values=ValuesClient(
            base_url=config["VALUES_BASE_URL"],
            timeout=config["VALUES_TIMEOUT"],
            ttl=config["VALUES_CACHE_SECONDS"],
            cache_dir=config["CACHE_DIR"],
        ),
        explainer=explainer,
        llm_limiter=SlidingWindowLimiter(config["LLM_CALLS_PER_HOUR"], 3600),
        request_limiter=SlidingWindowLimiter(config["REQUESTS_PER_MINUTE"], 60),
    )


def create_app(config: Optional[Dict[str, Any]] = None, services: Optional[Services] = None) -> Flask:
    app = Flask(__name__)
    app.config.update(load_config(config))

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if app.config["TRUST_PROXY"]:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)  # type: ignore[assignment]

    csrf.init_app(app)
    app.extensions["dynasty"] = services or build_services(app.config)

    from .routes import bp

    app.register_blueprint(bp)

    @app.template_filter("num")
    def _num(value: Any) -> str:
        try:
            return f"{float(value):,.0f}"
        except (TypeError, ValueError):
            return str(value)

    @app.after_request
    def _security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; "
            "form-action 'self'; frame-ancestors 'none'; base-uri 'self'",
        )
        return response

    @app.errorhandler(404)
    def _not_found(_error):
        return render_template("error.html", code=404, message="That page does not exist."), 404

    @app.errorhandler(CSRFError)
    def _csrf_error(_error):
        return (
            render_template(
                "error.html", code=400, message="Your form expired. Go back, refresh the page, and try again."
            ),
            400,
        )

    @app.errorhandler(429)
    def _too_many(_error):
        return render_template("error.html", code=429, message="Too many requests. Please slow down."), 429

    @app.errorhandler(500)
    def _server_error(_error):
        return render_template("error.html", code=500, message="Something went wrong on our side."), 500

    @app.before_request
    def _rate_limit():
        if request.method == "POST":
            key = request.remote_addr or "unknown"
            if not app.extensions["dynasty"].request_limiter.allow(key):
                return render_template("error.html", code=429, message="Too many requests. Please slow down."), 429
        return None

    return app
