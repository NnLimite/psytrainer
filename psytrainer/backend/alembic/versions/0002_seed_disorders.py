"""seed disorder reference

Стартовая выборка справочника МКБ-11 и DSM-5. Её нужно проверить и расширить с клиницистом (новые записи добавляйте
следующими миграциями, а эту не меняйте).

Revision ID: 0002
Revises: 0001
"""
import uuid

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

DISORDERS = [
    ('ICD-11', '6B00', 'Generalised anxiety disorder'),
    ('ICD-11', '6B01', 'Panic disorder'),
    ('ICD-11', '6B04', 'Social anxiety disorder'),
    ('ICD-11', '6B20', 'Obsessive-compulsive disorder'),
    ('ICD-11', '6B40', 'Post traumatic stress disorder'),
    ('ICD-11', '6B43', 'Adjustment disorder'),
    ('ICD-11', '6A70', 'Single episode depressive disorder'),
    ('ICD-11', '6A71', 'Recurrent depressive disorder'),
    ('DSM-5', 'F41.1', 'Generalized Anxiety Disorder'),
    ('DSM-5', 'F41.0', 'Panic Disorder'),
    ('DSM-5', 'F40.10', 'Social Anxiety Disorder'),
    ('DSM-5', 'F42', 'Obsessive-Compulsive Disorder'),
    ('DSM-5', 'F43.10', 'Posttraumatic Stress Disorder'),
    ('DSM-5', 'F43.20', 'Adjustment Disorders, unspecified'),
    ('DSM-5', 'F32.9', 'Major Depressive Disorder, single episode, unspecified'),
    ('DSM-5', 'F33.9', 'Major Depressive Disorder, recurrent episode, unspecified'),
]

disorder_ref = sa.table("disorder_ref", sa.column("id", sa.Uuid()), sa.column("classifier", sa.String()),
                        sa.column("code", sa.String()), sa.column("name", sa.String()))


def upgrade() -> None:
    op.bulk_insert(disorder_ref, [{"id": uuid.uuid4(), "classifier": c, "code": k, "name": n} for c, k, n in DISORDERS])


def downgrade() -> None:
    op.execute(disorder_ref.delete().where(sa.tuple_(disorder_ref.c.classifier, disorder_ref.c.code).in_(
        [(c, k) for c, k, _ in DISORDERS])))
