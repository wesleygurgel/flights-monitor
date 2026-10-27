FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Chromium headless para o calendário de preços do Google (exige navegador desde out/2026)
RUN playwright install --with-deps chromium

COPY app ./app
COPY migrations ./migrations
COPY alembic.ini .
COPY watchlist.yaml .

CMD ["python", "-m", "app.main", "serve", "--run-now"]
