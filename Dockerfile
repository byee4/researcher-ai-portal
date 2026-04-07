FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DJANGO_SETTINGS_MODULE=researcher_ai_portal.settings

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
COPY .vendor/researcher-ai /app/.vendor/researcher-ai
COPY .vendor/wheels /app/.vendor/wheels
RUN pip install -r requirements.txt

ARG RESEARCHER_AI_PIP_SPEC=researcher-ai==2.0.0
RUN pip install "${RESEARCHER_AI_PIP_SPEC}"
COPY . .
RUN chmod +x /app/scripts/start_web.sh /app/scripts/start_worker.sh /app/scripts/run_local_docker.sh

RUN python manage.py collectstatic --noinput 2>/dev/null || true

EXPOSE 8000
WORKDIR /app
CMD ["/app/scripts/start_web.sh"]
