FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DJANGO_SETTINGS_MODULE=config.settings

WORKDIR /app

COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements.txt

COPY . .

RUN mkdir -p /app/data && python -m compileall -q -j 0 bot config core general

RUN --mount=type=secret,id=dotenv,target=/app/.env python manage.py collectstatic --noinput

# TODO: перенести рабочую директорию из root в обычную; запускать всё приложение без root прав
