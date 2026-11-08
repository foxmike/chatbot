"""Durable companion identity and relationship; preserve all legacy records.

Revision ID: a12f907bc341
Revises: f687a630a8d1

Downgrade discards only the new durable state, never legacy/session data.
"""

import logging
from alembic import op
import sqlalchemy as sa

revision = "a12f907bc341"
down_revision = "f687a630a8d1"
branch_labels = depends_on = None


def upgrade():
    bind = op.get_bind()
    if not op.get_context().as_sql:
        inspector = sa.inspect(bind)
        for table in ("companion_identities", "companion_relationships"):
            if inspector.has_table(table):
                raise RuntimeError(f"{table} already exists; refusing to adopt or overwrite it")

    def common():
        return [
            sa.Column(
                "companion_id",
                sa.String(36),
                sa.ForeignKey("companions.companion_id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        ]

    op.create_table(
        "companion_identities",
        *common(),
        sa.Column("trait_adjustments", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    columns = []
    for name, default in [("type", "friend"), ("tone", "warm"), ("content_level", "moderate")]:
        columns.append(sa.Column(name, sa.String(), nullable=False, server_default=default))
    for name, default in [
        ("directness", "0.5"),
        ("warmth", "0.7"),
        ("initiative_level", "0.3"),
        ("depth", "0"),
    ]:
        columns.append(sa.Column(name, sa.Float(), nullable=False, server_default=default))
    op.create_table(
        "companion_relationships",
        *common(),
        *columns,
        sa.Column("completed_turns", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("first_interaction_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_interaction_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        "INSERT INTO companion_identities (companion_id) SELECT companion_id FROM companions ON CONFLICT DO NOTHING"
    )
    candidates = """SELECT c.companion_id, r.*,
        row_number() OVER (PARTITION BY c.companion_id ORDER BY r.updated_at DESC, r.created_at DESC, r.id DESC) AS precedence
        FROM companions c JOIN sessions s ON s.companion_id=c.companion_id AND s.user_id=c.user_id
        JOIN relationship_configs r ON r.session_id=s.id"""
    conflicts = f"""WITH candidates AS ({candidates}), conflicts AS (
        SELECT companion_id, count(DISTINCT (type,tone,directness,warmth,initiative_level,content_level)) AS variants
        FROM candidates GROUP BY companion_id
        HAVING count(DISTINCT (type,tone,directness,warmth,initiative_level,content_level)) > 1)
        SELECT c.companion_id,c.id,x.variants FROM candidates c JOIN conflicts x USING(companion_id) WHERE c.precedence=1"""
    if not op.get_context().as_sql:
        for row in bind.execute(sa.text(conflicts)):
            logging.getLogger("alembic.runtime.migration").warning(
                "Relationship conflict companion=%s chosen_config=%s distinct_candidates=%s", *row
            )
    op.execute(f"""WITH candidates AS ({candidates}), facts AS (
        SELECT c.companion_id, min(m.created_at) FILTER (WHERE m.role IN ('user','assistant')) AS first_at,
        max(m.created_at) FILTER (WHERE m.role IN ('user','assistant')) AS last_at,
        count(*) FILTER (WHERE m.role='assistant' AND (m.turn_id IS NULL OR NOT EXISTS (
            SELECT 1 FROM messages marker WHERE marker.session_id=m.session_id AND marker.turn_id=m.turn_id
            AND marker.role='system' AND marker.content LIKE '[Assistant turn interrupted.%'))) AS turns
        FROM companions c JOIN sessions s ON s.companion_id=c.companion_id AND s.user_id=c.user_id
        JOIN messages m ON m.session_id=s.id GROUP BY c.companion_id)
        INSERT INTO companion_relationships
        (companion_id,type,tone,directness,warmth,initiative_level,content_level,first_interaction_at,last_interaction_at,completed_turns)
        SELECT c.companion_id,coalesce(r.type,'friend'),coalesce(r.tone,'warm'),coalesce(r.directness,0.5),
        coalesce(r.warmth,0.7),coalesce(r.initiative_level,0.3),coalesce(r.content_level,'moderate'), f.first_at,f.last_at,coalesce(f.turns,0)
        FROM companions c LEFT JOIN candidates r ON r.companion_id=c.companion_id AND r.precedence=1
        LEFT JOIN facts f ON f.companion_id=c.companion_id ON CONFLICT DO NOTHING""")


def downgrade():
    op.drop_table("companion_relationships")
    op.drop_table("companion_identities")
