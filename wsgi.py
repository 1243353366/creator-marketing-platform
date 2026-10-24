"""WSGI entry point for production servers (gunicorn, uwsgi).

Config comes from the environment: DATABASE_URL and SECRET_KEY
are required; create_app refuses to boot with a weak or missing
SECRET_KEY, by design.
"""
from app import create_app

app = create_app()
