FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app app
COPY alembic alembic
COPY alembic.ini .
COPY docs docs
COPY examples examples
RUN useradd -m psy && mkdir -p /srv/storage && chown -R psy /srv/storage
USER psy
EXPOSE 8000
# миграции выполняются при старте; для нескольких реплик вынесите их в отдельный шаг деплоя
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app_factory --factory --host 0.0.0.0 --port 8000 --proxy-headers"]
