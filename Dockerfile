FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=config.settings

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/data

RUN --mount=type=secret,id=dotenv,target=/app/.env python manage.py collectstatic --noinput

# TODO: перенести рабочую директорию из root в обычную; запускать всё приложение без root прав
