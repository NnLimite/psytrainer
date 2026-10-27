.PHONY: help test pg-test smoke api migrate erd openapi docs-check

help:
	@echo "make test       - тесты бэкенда (SQLite)"
	@echo "make pg-test    - миграции и тесты на встроенном PostgreSQL"
	@echo "make smoke      - сквозная проверка по реальному HTTP"
	@echo "make api        - запустить API с перезагрузкой (нужны PostgreSQL и .env)"
	@echo "make migrate    - применить миграции Alembic"
	@echo "make erd        - перегенерировать docs/erd.md из моделей"
	@echo "make openapi    - перегенерировать контракт backend/docs/openapi.yaml"

test:
	cd backend && python -m pytest -q

pg-test:
	cd backend && python scripts/test_on_postgres.py

smoke:
	cd backend && python scripts/smoke_e2e.py

api:
	cd backend && uvicorn app.main:app_factory --factory --reload

migrate:
	cd backend && alembic upgrade head

erd:
	cd backend && python scripts/gen_erd.py > ../docs/erd.md

openapi:
	cd backend && python docs/build_openapi.py docs/openapi.yaml
