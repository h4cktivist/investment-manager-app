"""нейтральный код формата импорта

Переименовывает код формата партий импорта и префикс внешних идентификаторов
операций. Префикс важно переписать: по нему работает защита от повторного
импорта одного и того же файла.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-02
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW_FORMAT = "broker_csv"
NEW_PREFIX = "csv"


def upgrade() -> None:
    op.alter_column(
        "import_batches",
        "format",
        existing_type=sa.String(32),
        existing_nullable=False,
        server_default=NEW_FORMAT,
    )
    op.execute(
        sa.text("UPDATE import_batches SET format = :new WHERE format <> :new").bindparams(
            new=NEW_FORMAT
        )
    )
    # внешний идентификатор имеет вид «префикс:хеш:номер» — меняем только префикс,
    # чтобы защита от повторного импорта одного файла продолжала работать
    op.execute(
        sa.text(
            "UPDATE transactions SET external_id = :prefix || substring(external_id "
            "from position(':' in external_id)) "
            "WHERE external_id IS NOT NULL "
            "AND position(':' in external_id) > 0 "
            "AND external_id NOT LIKE :pattern"
        ).bindparams(prefix=NEW_PREFIX, pattern=f"{NEW_PREFIX}:%")
    )


def downgrade() -> None:
    # префикс идентификаторов обратно не восстанавливается: прежнее значение
    # в схеме больше не хранится, а на работу приложения он не влияет
    pass
