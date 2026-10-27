"""Начальный справочник расстройств. Это небольшая стартовая выборка: её нужно проверить и расширить с клиницистом."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import DisorderRef

DISORDERS: list[tuple[str, str, str]] = [
    ("ICD-11", "6B00", "Generalised anxiety disorder"),
    ("ICD-11", "6B01", "Panic disorder"),
    ("ICD-11", "6B04", "Social anxiety disorder"),
    ("ICD-11", "6B20", "Obsessive-compulsive disorder"),
    ("ICD-11", "6B40", "Post traumatic stress disorder"),
    ("ICD-11", "6B43", "Adjustment disorder"),
    ("ICD-11", "6A70", "Single episode depressive disorder"),
    ("ICD-11", "6A71", "Recurrent depressive disorder"),
    ("DSM-5", "F41.1", "Generalized Anxiety Disorder"),
    ("DSM-5", "F41.0", "Panic Disorder"),
    ("DSM-5", "F40.10", "Social Anxiety Disorder"),
    ("DSM-5", "F42", "Obsessive-Compulsive Disorder"),
    ("DSM-5", "F43.10", "Posttraumatic Stress Disorder"),
    ("DSM-5", "F43.20", "Adjustment Disorders, unspecified"),
    ("DSM-5", "F32.9", "Major Depressive Disorder, single episode, unspecified"),
    ("DSM-5", "F33.9", "Major Depressive Disorder, recurrent episode, unspecified"),
]


async def ensure_reference_data(db: AsyncSession) -> int:
    existing = {(r.classifier, r.code) for r in (await db.execute(select(DisorderRef))).scalars().all()}
    added = 0
    for classifier, code, name in DISORDERS:
        if (classifier, code) not in existing:
            db.add(DisorderRef(classifier=classifier, code=code, name=name))
            added += 1
    await db.commit()
    return added
