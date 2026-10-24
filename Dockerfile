# Self-contained image: Flask app + embedded PostgreSQL (pgserver).
# Runs on Cloudflare Containers (linux/amd64), no external database.
FROM python:3.11-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PYTHONUNBUFFERED=1
EXPOSE 8000
# pgserver keeps its data dir across sleeps of the same instance.
CMD ["sh", "-c", "export DATABASE_URL=$(python -c 'from pgserver import PostgresServer; from pathlib import Path; print(PostgresServer(Path(\"/pgdata\")).get_uri())') && alembic upgrade head && exec gunicorn --workers 2 --threads 2 --bind 0.0.0.0:8000 wsgi:app"]
