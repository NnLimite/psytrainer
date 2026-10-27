"""Контракт и код не должны расходиться: сверяем docs/openapi.yaml со схемой, которую генерирует FastAPI."""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from app.main import create_app

CONTRACT = yaml.safe_load((Path(__file__).resolve().parents[1] / "docs" / "openapi.yaml").read_text(encoding="utf-8"))
PREFIX = "/api/v1"


def norm(path: str) -> str:
    return re.sub(r"{[^}]+}", "{}", path)


def contract_ops():
    ops = {}
    for path, item in CONTRACT["paths"].items():
        for method, op in item.items():
            q = {p["name"] for p in op.get("parameters", []) if p["in"] == "query"}
            ok = next(int(c) for c in op["responses"] if c.startswith(("2", "3")))
            ops[(method.upper(), PREFIX + norm(path))] = {"query": q, "status": ok, "body": "requestBody" in op}
    return ops


def app_ops(container):
    spec = create_app(container).openapi()
    ops = {}
    for path, item in spec["paths"].items():
        for method, op in item.items():
            q = {p["name"] for p in op.get("parameters", []) if p["in"] == "query"}
            ok = next(int(c) for c in op["responses"] if c.startswith(("2", "3")))
            ops[(method.upper(), norm(path))] = {"query": q, "status": ok, "body": "requestBody" in op}
    return ops


def test_every_contract_operation_is_implemented_and_vice_versa(container):
    designed, actual = contract_ops(), app_ops(container)
    assert sorted(set(designed) - set(actual)) == [], "в контракте есть, в коде нет"
    assert sorted(set(actual) - set(designed)) == [], "в коде есть, в контракте нет"


def test_query_params_status_codes_and_bodies_match(container):
    designed, actual = contract_ops(), app_ops(container)
    diffs = []
    for key in sorted(designed):
        d, a = designed[key], actual.get(key)
        if a is None:
            continue
        if d["query"] != a["query"]:
            diffs.append(f"{key}: query контракт={sorted(d['query'])} код={sorted(a['query'])}")
        if d["status"] != a["status"]:
            diffs.append(f"{key}: статус контракт={d['status']} код={a['status']}")
        if d["body"] != a["body"]:
            diffs.append(f"{key}: тело запроса контракт={d['body']} код={a['body']}")
    assert diffs == []


def test_erd_doc_is_in_sync_with_models():
    """docs/erd.md генерируется из моделей: если модели изменились, нужно выполнить `make erd`."""
    import importlib.util
    import pytest

    erd = Path(__file__).resolve().parents[2] / "docs" / "erd.md"
    if not erd.exists():  # бэкенд запущен отдельно от монорепозитория
        pytest.skip("docs/erd.md не найден")
    spec = importlib.util.spec_from_file_location("gen_erd", Path(__file__).resolve().parents[1] / "scripts" / "gen_erd.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert erd.read_text(encoding="utf-8") == mod.main() + "\n", "docs/erd.md устарел: выполните make erd"
