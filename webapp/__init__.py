from __future__ import annotations

import os
from datetime import timedelta

from flask import Flask


def create_app() -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")

    app.secret_key = os.environ.get("IPO_WEB_SECRET", "dev-only-change-me")
    app.permanent_session_lifetime = timedelta(days=30)
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_HTTPONLY"] = True

    from .routes import bp

    app.register_blueprint(bp)
    return app
