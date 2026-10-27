# PsyTrainer

Платформа для тренировки навыков консультирования: студент-психолог ведёт диалог с ИИ-пациентом (локальная Qwen через
Ollama), система меняет состояние пациента по правилам и разбирает сессию по рубрике.

## Структура репозитория

```
psytrainer/
├── backend/            # FastAPI, PostgreSQL, Redis, Alembic: весь серверный код, тесты, контракт API
│   ├── app/            #   engine/ (ядро), services/, routers/, models.py, container.py ...
│   ├── alembic/        #   миграции
│   ├── tests/          #   80 тестов
│   ├── docs/           #   openapi.yaml и его генератор
│   ├── examples/       #   пример кейса
│   └── scripts/        #   проверка на PostgreSQL, сквозная проверка, генератор ER-диаграмм
├── frontend/           # сюда ваши компоненты на TSX (заготовка и инструкция внутри)
├── docs/               # архитектура, модель случая, API, ER-диаграммы, статус
├── .github/workflows/  # CI (не проверялся)
├── Makefile            # короткие команды: make test, make api, make migrate ...
└── README.md
```

## Быстрый старт (без Docker)

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env            # задайте PSY_OLLAMA_MODEL, PSY_DATABASE_URL (новая чистая БД), секреты
alembic upgrade head
python -m app.cli create-user --email admin@x.ru --role platform_admin
uvicorn app.main:app_factory --factory --reload    # http://localhost:8000/docs
```

Проверка: `make test` (SQLite, около 20 секунд) и `make pg-test` (настоящий PostgreSQL, миграции вверх-вниз).
Подробности запуска, настройки и ограничения: [`backend/README.md`](backend/README.md).

## С чего читать

1. [`docs/architecture.md`](docs/architecture.md): как всё устроено.
2. [`docs/case-model.md`](docs/case-model.md): как «думает» ИИ-пациент.
3. [`docs/api.md`](docs/api.md): API целиком.
4. [`docs/roadmap.md`](docs/roadmap.md): что осталось сделать до рабочего продукта.
