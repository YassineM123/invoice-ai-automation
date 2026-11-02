FROM python:3.12-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=10
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libharfbuzz-subset0 \
    libjpeg62-turbo \
    libffi-dev \
    libcairo2 \
    shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

RUN mkdir -p storage/uploads storage/outputs runtime/uploads runtime/outputs runtime/whatsapp-session runtime/whatsapp-downloads runtime/whatsapp-generated runtime/telegram-downloads runtime/telegram-sessions runtime/telegram-generated

FROM base AS api

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM base AS telegram-bot

CMD ["python", "-m", "app.bot.telegram_bot"]

FROM base AS whatsapp-bot

RUN playwright install --with-deps chromium

CMD ["python", "-m", "app.bot.whatsapp_web_bot"]
