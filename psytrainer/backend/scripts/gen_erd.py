"""Генерирует ER-диаграммы (Mermaid) прямо из SQLAlchemy-моделей, чтобы документация не расходилась с кодом.
Запуск: python scripts/gen_erd.py > ../docs/erd.md"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import Boolean, Date, DateTime, Float, Integer, String, Text, Uuid  # noqa: E402
from sqlalchemy import JSON  # noqa: E402

from app.models import Base  # noqa: E402

GROUPS = {
    "Организации и доступ": ["organization", "user_account", "study_group", "group_member", "consent", "subscription",
                             "invitation", "refresh_token", "one_time_token"],
    "Кейсы и сессии": ["disorder_ref", "case_template", "case_version", "case_disorder", "patient_session", "turn",
                       "state_snapshot", "safety_event", "student_formulation", "user_account"],
    "Оценка и платформа": ["rubric", "rubric_criterion", "assessment", "criterion_score", "patient_session", "turn",
                           "job", "audit_log", "prompt_template", "llm_call", "payment_event"],
}


def mtype(col) -> str:
    t = col.type
    for cls, name in ((Uuid, "uuid"), (Text, "text"), (String, "string"), (Integer, "int"), (Float, "float"),
                      (Boolean, "bool"), (DateTime, "datetime"), (Date, "date"), (JSON, "json")):
        if isinstance(t, cls):
            return name
    return "string"


def diagram(tables: list[str]) -> str:
    out = ["```mermaid", "erDiagram"]
    for name in tables:
        t = Base.metadata.tables[name]
        out.append(f"  {name.upper()} {{")
        for c in t.columns:
            keys = [k for k, on in (("PK", c.primary_key), ("FK", bool(c.foreign_keys))) if on]
            out.append(f"    {mtype(c)} {c.name}" + (f" {', '.join(keys)}" if keys else ""))
        out.append("  }")
    for name in tables:
        for c in Base.metadata.tables[name].columns:
            for fk in c.foreign_keys:
                parent = fk.column.table.name
                if parent in tables and parent != name:
                    left = "|o" if c.nullable else "||"
                    out.append(f"  {parent.upper()} {left}--o{{ {name.upper()} : \"{c.name}\"")
    out.append("```")
    return "\n".join(out)


def main() -> str:
    covered = {t for g in GROUPS.values() for t in g}
    missing = set(Base.metadata.tables) - covered
    assert not missing, f"таблицы не попали ни в одну группу ER-диаграммы: {sorted(missing)}"
    parts = ["# ER-диаграммы", "",
             "Генерируются из `backend/app/models.py` командой `make erd`. Не правьте вручную.", ""]
    for title, tables in GROUPS.items():
        parts += [f"## {title}", "", diagram(tables), ""]
    return "\n".join(parts)


if __name__ == "__main__":
    print(main())
