"""Initial clean migration

Revision ID: 9ae6e7802ab2
Revises: 
Create Date: 2026-06-07 01:24:01.074354

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import pgvector


# revision identifiers, used by Alembic.
revision: str = '9ae6e7802ab2'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table('users',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('external_id', sa.String(), nullable=False),
    sa.Column('display_name', sa.String(), nullable=False),
    sa.Column('username', sa.String(length=64), nullable=False),
    sa.Column('email', sa.String(length=255), nullable=False),
    sa.Column('password_hash', sa.String(length=128), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('email'),
    sa.UniqueConstraint('username')
    )
    op.create_index(op.f('ix_users_external_id'), 'users', ['external_id'], unique=True)
    op.create_table('entities',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('entity_type', sa.String(), nullable=False),
    sa.Column('metadata_json', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_entities_name'), 'entities', ['name'], unique=False)
    op.create_table('initiative_settings',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('user_id', sa.String(length=64), nullable=False),
    sa.Column('level', sa.String(length=32), nullable=False),
    sa.Column('quiet_hours_start', sa.String(length=5), nullable=True),
    sa.Column('quiet_hours_end', sa.String(length=5), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id')
    )
    op.create_table('narrative_threads',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('session_id', sa.String(length=64), nullable=False),
    sa.Column('label', sa.String(length=255), nullable=False),
    sa.Column('turns', sa.JSON(), nullable=False),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('relevance_score', sa.Float(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_referenced_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_narrative_threads_session_id'), 'narrative_threads', ['session_id'], unique=False)
    op.create_table('observability_traces',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('trace_id', sa.String(length=36), nullable=False),
    sa.Column('turn_id', sa.String(length=36), nullable=False),
    sa.Column('session_id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=True),
    sa.Column('model_provider', sa.String(length=64), nullable=True),
    sa.Column('model_id', sa.String(length=128), nullable=True),
    sa.Column('task_class', sa.String(length=32), nullable=True),
    sa.Column('prompt_tokens', sa.Integer(), nullable=False),
    sa.Column('completion_tokens', sa.Integer(), nullable=False),
    sa.Column('cost_usd_micros', sa.Integer(), nullable=False),
    sa.Column('total_latency_ms', sa.Float(), nullable=False),
    sa.Column('retrieve_latency_ms', sa.Float(), nullable=False),
    sa.Column('prompt', sa.Text(), nullable=False),
    sa.Column('routing_decision', sa.JSON(), nullable=False),
    sa.Column('retrieval_snippets', sa.JSON(), nullable=False),
    sa.Column('steps', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_observability_traces_session_id'), 'observability_traces', ['session_id'], unique=False)
    op.create_index(op.f('ix_observability_traces_trace_id'), 'observability_traces', ['trace_id'], unique=True)
    op.create_index(op.f('ix_observability_traces_turn_id'), 'observability_traces', ['turn_id'], unique=False)
    op.create_index(op.f('ix_observability_traces_user_id'), 'observability_traces', ['user_id'], unique=False)
    op.create_table('observability_usage',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('session_id', sa.String(length=36), nullable=False),
    sa.Column('model_provider', sa.String(length=64), nullable=False),
    sa.Column('model_id', sa.String(length=128), nullable=False),
    sa.Column('turn_count', sa.Integer(), nullable=False),
    sa.Column('prompt_tokens', sa.Integer(), nullable=False),
    sa.Column('completion_tokens', sa.Integer(), nullable=False),
    sa.Column('cost_usd_micros', sa.Integer(), nullable=False),
    sa.Column('avg_latency_ms', sa.Float(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('session_id', 'model_provider', 'model_id')
    )
    op.create_index(op.f('ix_observability_usage_model_id'), 'observability_usage', ['model_id'], unique=False)
    op.create_index(op.f('ix_observability_usage_model_provider'), 'observability_usage', ['model_provider'], unique=False)
    op.create_index(op.f('ix_observability_usage_session_id'), 'observability_usage', ['session_id'], unique=False)
    op.create_table('rituals',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('session_id', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('ritual_type', sa.String(length=20), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('schedule_description', sa.String(length=255), nullable=False),
    sa.Column('last_triggered_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('next_trigger_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('trigger_prompt', sa.Text(), nullable=True),
    sa.Column('metadata_', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_rituals_session_id'), 'rituals', ['session_id'], unique=False)
    op.create_table('timeline_entries',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('session_id', sa.String(length=64), nullable=False),
    sa.Column('entry_type', sa.String(length=20), nullable=False),
    sa.Column('title', sa.String(length=255), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('turn_id', sa.String(length=64), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('tags', sa.JSON(), nullable=True),
    sa.Column('metadata_', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_timeline_entries_session_id'), 'timeline_entries', ['session_id'], unique=False)
    op.create_table('voice_preferences',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.String(length=64), nullable=False),
    sa.Column('tts_provider_id', sa.String(length=64), nullable=True),
    sa.Column('tts_model_id', sa.String(length=128), nullable=True),
    sa.Column('stt_provider_id', sa.String(length=64), nullable=True),
    sa.Column('stt_model_id', sa.String(length=128), nullable=True),
    sa.Column('voice_speed', sa.Float(), nullable=True),
    sa.Column('voice_pitch', sa.Float(), nullable=True),
    sa.Column('voice_enabled', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id')
    )
    op.create_table('agency_notifications',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('user_id', sa.String(length=64), nullable=False),
    sa.Column('notif_type', sa.String(length=64), nullable=False),
    sa.Column('title', sa.String(length=255), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('read', sa.Boolean(), nullable=False),
    sa.Column('dismissed', sa.Boolean(), nullable=False),
    sa.Column('ritual_metadata', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_agency_notifications_user_id'), 'agency_notifications', ['user_id'], unique=False)
    op.create_table('agency_rituals',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('user_id', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('ritual_type', sa.String(length=64), nullable=False),
    sa.Column('cron_expression', sa.String(length=128), nullable=True),
    sa.Column('message_template', sa.Text(), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('ritual_metadata', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_agency_rituals_user_id'), 'agency_rituals', ['user_id'], unique=False)
    op.create_table('agency_timeline',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('user_id', sa.String(length=64), nullable=False),
    sa.Column('event_type', sa.String(length=64), nullable=False),
    sa.Column('title', sa.String(length=255), nullable=False),
    sa.Column('occurs_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('context', sa.JSON(), nullable=True),
    sa.Column('recurring', sa.Boolean(), nullable=False),
    sa.Column('recurrence_rule', sa.String(length=128), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_agency_timeline_user_id'), 'agency_timeline', ['user_id'], unique=False)
    op.create_table('user_sessions',
    sa.Column('session_id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('token_hash', sa.String(length=128), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('revoked', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('session_id')
    )
    op.create_table('image_memories',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('image_id', sa.String(length=36), nullable=False),
    sa.Column('source_type', sa.String(length=20), nullable=False),
    sa.Column('turn_id', sa.String(length=64), nullable=True),
    sa.Column('session_id', sa.String(length=64), nullable=True),
    sa.Column('user_id', sa.UUID(), nullable=True),
    sa.Column('image_data', sa.LargeBinary(), nullable=True),
    sa.Column('image_url', sa.String(length=1024), nullable=True),
    sa.Column('mime_type', sa.String(length=64), nullable=False),
    sa.Column('prompt_used', sa.Text(), nullable=True),
    sa.Column('seed_used', sa.Integer(), nullable=True),
    sa.Column('model_used', sa.String(length=128), nullable=True),
    sa.Column('width', sa.Integer(), nullable=True),
    sa.Column('height', sa.Integer(), nullable=True),
    sa.Column('generation_time_ms', sa.Float(), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('detected_objects', sa.JSON(), nullable=True),
    sa.Column('detected_text', sa.Text(), nullable=True),
    sa.Column('scene_category', sa.String(length=128), nullable=True),
    sa.Column('understanding_confidence', sa.Float(), nullable=True),
    sa.Column('processing_time_ms', sa.Float(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("source_type IN ('generated', 'uploaded', 'understood')", name='ck_image_memories_source_type'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('image_id')
    )
    op.create_table('rooms',
    sa.Column('room_id', sa.String(length=64), nullable=False),
    sa.Column('user_id', sa.String(length=64), nullable=False),
    sa.Column('companion_id', sa.String(length=64), nullable=True),
    sa.Column('room_type', sa.String(length=20), nullable=False),
    sa.Column('display_name', sa.String(length=255), nullable=False),
    sa.Column('persist_to_durable', sa.Boolean(), nullable=False),
    sa.Column('memory_scope', sa.String(length=20), nullable=False),
    sa.Column('max_context_turns', sa.Integer(), nullable=False),
    sa.Column('include_embeddings', sa.Boolean(), nullable=False),
    sa.Column('scene_id', sa.String(length=64), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('metadata_json', sa.JSON(), nullable=True),
    sa.Column('session_id', sa.UUID(), nullable=True),
    sa.ForeignKeyConstraint(['scene_id'], ['scenes.scene_id'], name='rooms_scene_id_fkey', use_alter=True),
    sa.PrimaryKeyConstraint('room_id')
    )
    op.create_index(op.f('ix_rooms_user_id'), 'rooms', ['user_id'], unique=False)
    op.create_table('sessions',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('companion_id', sa.String(length=36), nullable=True),
    sa.Column('room_id', sa.String(length=64), nullable=True),
    sa.Column('title', sa.String(), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('mood_state', sa.JSON(), nullable=True),
    sa.Column('user_state', sa.JSON(), nullable=True),
    sa.Column('conversation_mode', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['room_id'], ['rooms.room_id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_sessions_companion_id'), 'sessions', ['companion_id'], unique=False)
    op.create_index(op.f('ix_sessions_room_id'), 'sessions', ['room_id'], unique=False)
    op.create_table('companions',
    sa.Column('companion_id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('persona', sa.Text(), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('avatar_url', sa.String(length=512), nullable=True),
    sa.Column('backstory', sa.Text(), nullable=True),
    sa.Column('personality_sliders', sa.JSON(), nullable=True),
    sa.Column('humor_style', sa.String(length=20), nullable=True),
    sa.Column('communication_style', sa.String(length=30), nullable=True),
    sa.Column('favorite_topics', sa.JSON(), nullable=True),
    sa.Column('appearance_id', sa.String(length=36), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['appearance_id'], ['appearances.appearance_id'], name='companions_appearance_id_fkey', use_alter=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('companion_id')
    )
    op.create_table('appearances',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('appearance_id', sa.String(length=36), nullable=False),
    sa.Column('companion_id', sa.String(length=36), nullable=True),
    sa.Column('character_seed', sa.Integer(), nullable=False),
    sa.Column('character_description', sa.Text(), nullable=False),
    sa.Column('visual_traits', sa.JSON(), nullable=False),
    sa.Column('art_style', sa.String(length=128), nullable=False),
    sa.Column('color_palette', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['companion_id'], ['companions.companion_id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('appearance_id')
    )
    op.create_table('scenes',
    sa.Column('scene_id', sa.String(length=64), nullable=False),
    sa.Column('room_id', sa.String(length=64), nullable=False),
    sa.Column('location', sa.String(length=255), nullable=False),
    sa.Column('activity', sa.String(length=255), nullable=False),
    sa.Column('tone', sa.String(length=20), nullable=False),
    sa.Column('intensity', sa.Integer(), nullable=False),
    sa.Column('is_paused', sa.Boolean(), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('paused_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('resumed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('participants', sa.JSON(), nullable=True),
    sa.Column('scene_context', sa.Text(), nullable=True),
    sa.Column('extra_metadata', sa.JSON(), nullable=True),
    sa.ForeignKeyConstraint(['room_id'], ['rooms.room_id'], ),
    sa.PrimaryKeyConstraint('scene_id')
    )
    op.create_index(op.f('ix_scenes_room_id'), 'scenes', ['room_id'], unique=False)
    op.create_table('bot_mood_logs',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('session_id', sa.UUID(), nullable=False),
    sa.Column('mood', sa.String(), nullable=False),
    sa.Column('energy', sa.Float(), nullable=False),
    sa.Column('closeness', sa.Float(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['session_id'], ['sessions.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_bot_mood_logs_session_id'), 'bot_mood_logs', ['session_id'], unique=False)
    op.create_table('character_profiles',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('session_id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('backstory', sa.Text(), nullable=False),
    sa.Column('personality_sliders', sa.JSON(), nullable=False),
    sa.Column('humor_style', sa.String(), nullable=False),
    sa.Column('communication_style', sa.String(), nullable=False),
    sa.Column('favorite_topics', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['session_id'], ['sessions.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_character_profiles_session_id'), 'character_profiles', ['session_id'], unique=False)
    op.create_table('identity_states',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('session_id', sa.UUID(), nullable=False),
    sa.Column('fixed', sa.JSON(), nullable=False),
    sa.Column('slow', sa.JSON(), nullable=False),
    sa.Column('mood', sa.JSON(), nullable=False),
    sa.Column('last_updated', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['session_id'], ['sessions.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_identity_states_session_id'), 'identity_states', ['session_id'], unique=False)
    op.create_table('relationship_configs',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('session_id', sa.UUID(), nullable=False),
    sa.Column('type', sa.String(), nullable=False),
    sa.Column('tone', sa.String(), nullable=False),
    sa.Column('directness', sa.Float(), nullable=False),
    sa.Column('warmth', sa.Float(), nullable=False),
    sa.Column('initiative_level', sa.Float(), nullable=False),
    sa.Column('content_level', sa.String(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['session_id'], ['sessions.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_relationship_configs_session_id'), 'relationship_configs', ['session_id'], unique=False)
    op.create_table('user_state_logs',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('session_id', sa.UUID(), nullable=False),
    sa.Column('primary_state', sa.String(), nullable=True),
    sa.Column('confidence', sa.Float(), nullable=False),
    sa.Column('evidence_summary', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['session_id'], ['sessions.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_user_state_logs_session_id'), 'user_state_logs', ['session_id'], unique=False)
    op.create_table('messages',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('session_id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=True),
    sa.Column('companion_id', sa.String(length=36), nullable=True),
    sa.Column('role', sa.String(), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('embedding', pgvector.sqlalchemy.vector.VECTOR(), nullable=True),
    sa.Column('token_count', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("role IN ('user', 'assistant', 'system', 'tool')", name='ck_messages_role'),
    sa.ForeignKeyConstraint(['companion_id'], ['companions.companion_id'], ),
    sa.ForeignKeyConstraint(['session_id'], ['sessions.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_messages_companion_id'), 'messages', ['companion_id'], unique=False)
    op.create_index(op.f('ix_messages_user_id'), 'messages', ['user_id'], unique=False)
    op.create_table('episodes',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('session_id', sa.UUID(), nullable=False),
    sa.Column('title', sa.String(), nullable=False),
    sa.Column('summary', sa.Text(), nullable=True),
    sa.Column('start_message_id', sa.UUID(), nullable=True),
    sa.Column('end_message_id', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['end_message_id'], ['messages.id'], ),
    sa.ForeignKeyConstraint(['session_id'], ['sessions.id'], ),
    sa.ForeignKeyConstraint(['start_message_id'], ['messages.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('outfit_records',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('outfit_id', sa.String(length=36), nullable=False),
    sa.Column('appearance_id', sa.String(length=36), nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('visual_tags', sa.JSON(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['appearance_id'], ['appearances.appearance_id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('outfit_id')
    )
    op.create_table('semantic_facts',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('fact_text', sa.Text(), nullable=False),
    sa.Column('embedding', pgvector.sqlalchemy.vector.VECTOR(), nullable=True),
    sa.Column('source_message_id', sa.UUID(), nullable=True),
    sa.Column('source_type', sa.String(), server_default=sa.text("'inferred'"), nullable=False),
    sa.Column('source_turn_id', sa.UUID(), nullable=True),
    sa.Column('confidence', sa.Float(), nullable=False),
    sa.Column('privacy_level', sa.String(), server_default=sa.text("'temporary'"), nullable=False),
    sa.Column('pinned', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("privacy_level IN ('private', 'temporary', 'permanent')", name='ck_semantic_facts_privacy_level'),
    sa.CheckConstraint("source_type IN ('explicit', 'inferred')", name='ck_semantic_facts_source_type'),
    sa.ForeignKeyConstraint(['source_message_id'], ['messages.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    # ### end Alembic commands ###
    op.create_foreign_key('rooms_session_id_fkey', 'rooms', 'sessions', ['session_id'], ['id'])
    op.create_foreign_key('sessions_companion_id_fkey', 'sessions', 'companions', ['companion_id'], ['companion_id'])

def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_constraint('rooms_session_id_fkey', 'rooms', type_='foreignkey')
    op.drop_constraint('sessions_companion_id_fkey', 'sessions', type_='foreignkey')
    op.drop_table('semantic_facts')
    op.drop_table('outfit_records')
    op.drop_table('episodes')
    op.drop_index(op.f('ix_messages_user_id'), table_name='messages')
    op.drop_index(op.f('ix_messages_companion_id'), table_name='messages')
    op.drop_table('messages')
    op.drop_index(op.f('ix_user_state_logs_session_id'), table_name='user_state_logs')
    op.drop_table('user_state_logs')
    op.drop_index(op.f('ix_relationship_configs_session_id'), table_name='relationship_configs')
    op.drop_table('relationship_configs')
    op.drop_index(op.f('ix_identity_states_session_id'), table_name='identity_states')
    op.drop_table('identity_states')
    op.drop_index(op.f('ix_character_profiles_session_id'), table_name='character_profiles')
    op.drop_table('character_profiles')
    op.drop_index(op.f('ix_bot_mood_logs_session_id'), table_name='bot_mood_logs')
    op.drop_table('bot_mood_logs')
    op.drop_index(op.f('ix_scenes_room_id'), table_name='scenes')
    op.drop_table('scenes')
    op.drop_table('appearances')
    op.drop_table('companions')
    op.drop_index(op.f('ix_sessions_room_id'), table_name='sessions')
    op.drop_index(op.f('ix_sessions_companion_id'), table_name='sessions')
    op.drop_table('sessions')
    op.drop_index(op.f('ix_rooms_user_id'), table_name='rooms')
    op.drop_table('rooms')
    op.drop_table('image_memories')
    op.drop_table('user_sessions')
    op.drop_index(op.f('ix_agency_timeline_user_id'), table_name='agency_timeline')
    op.drop_table('agency_timeline')
    op.drop_index(op.f('ix_agency_rituals_user_id'), table_name='agency_rituals')
    op.drop_table('agency_rituals')
    op.drop_index(op.f('ix_agency_notifications_user_id'), table_name='agency_notifications')
    op.drop_table('agency_notifications')
    op.drop_table('voice_preferences')
    op.drop_index(op.f('ix_timeline_entries_session_id'), table_name='timeline_entries')
    op.drop_table('timeline_entries')
    op.drop_index(op.f('ix_rituals_session_id'), table_name='rituals')
    op.drop_table('rituals')
    op.drop_index(op.f('ix_observability_usage_session_id'), table_name='observability_usage')
    op.drop_index(op.f('ix_observability_usage_model_provider'), table_name='observability_usage')
    op.drop_index(op.f('ix_observability_usage_model_id'), table_name='observability_usage')
    op.drop_table('observability_usage')
    op.drop_index(op.f('ix_observability_traces_user_id'), table_name='observability_traces')
    op.drop_index(op.f('ix_observability_traces_turn_id'), table_name='observability_traces')
    op.drop_index(op.f('ix_observability_traces_trace_id'), table_name='observability_traces')
    op.drop_index(op.f('ix_observability_traces_session_id'), table_name='observability_traces')
    op.drop_table('observability_traces')
    op.drop_index(op.f('ix_narrative_threads_session_id'), table_name='narrative_threads')
    op.drop_table('narrative_threads')
    op.drop_table('initiative_settings')
    op.drop_index(op.f('ix_entities_name'), table_name='entities')
    op.drop_table('entities')
    op.drop_index(op.f('ix_users_external_id'), table_name='users')
    op.drop_table('users')
    op.execute("DROP EXTENSION IF EXISTS vector")
    # ### end Alembic commands ###
