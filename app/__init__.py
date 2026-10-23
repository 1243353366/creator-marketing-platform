"""App factory. Fails closed: no Postgres, no secret key, no boot."""
import os

from flask import Flask


def create_app(config=None):
    app = Flask(__name__)
    app.config.update(
        DATABASE_URL=os.environ.get("DATABASE_URL", ""),
        SECRET_KEY=os.environ.get("SECRET_KEY", ""),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("FLASK_ENV") == "production",
        TESTING=False,
        AI_PROVIDER_URL=os.environ.get("AI_PROVIDER_URL", ""),
        AI_PROVIDER_KEY=os.environ.get("AI_PROVIDER_KEY", ""),
        AI_BUDGET_CENTS=int(os.environ.get("AI_BUDGET_CENTS", "200")),
        PAYMENT_WEBHOOK_SECRET=os.environ.get(
            "PAYMENT_WEBHOOK_SECRET", ""),
    )
    if config:
        app.config.update(config)
    url = app.config["DATABASE_URL"]
    if not url.startswith(("postgresql://", "postgresql+psycopg2://")):
        raise RuntimeError(
            "DATABASE_URL must be a postgresql:// URL. There is no "
            "fallback database by design; refusing to boot.")
    if not app.config["SECRET_KEY"] or len(
            app.config["SECRET_KEY"]) < 16:
        raise RuntimeError(
            "SECRET_KEY is required (>=16 chars). Refusing to boot "
            "with an insecure default.")
    from .db import init_db
    init_db(url)
    from .routes import register
    register(app)
    return app
