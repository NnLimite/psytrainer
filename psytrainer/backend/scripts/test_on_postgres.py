"""Поднимает встроенный PostgreSQL (pip install pgserver), применяет миграции, проверяет, что модели и миграции
не расходятся, и прогоняет весь набор тестов на настоящей БД. Запуск: python scripts/test_on_postgres.py"""
import os
import subprocess
import sys
import tempfile

import pgserver

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
srv = pgserver.get_server(tempfile.mkdtemp())
host = srv.get_uri().split("host=")[1]
url = f"postgresql+asyncpg://postgres@/postgres?host={host}"
env = {**os.environ, "PSY_DATABASE_URL": url, "PSY_TEST_DATABASE_URL": url}


def run(*cmd: str) -> int:
    print("$", " ".join(cmd), flush=True)
    return subprocess.run(cmd, cwd=root, env=env).returncode


code = (run(sys.executable, "-m", "alembic", "upgrade", "head")
        or run(sys.executable, "-m", "alembic", "check")
        or run(sys.executable, "-m", "alembic", "downgrade", "base")
        or run(sys.executable, "-m", "alembic", "upgrade", "head")
        or run(sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"))
srv.cleanup()
sys.exit(code)
