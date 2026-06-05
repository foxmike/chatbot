"""
Integration tests for the rooms module.
These tests verify the complete room system functionality with real database.
"""

import pytest
import asyncio
import tempfile
import os
from pathlib import Path

from chatbot.rooms import create_room, get_room, get_user_rooms, update_room_memory_policy
from chatbot.rooms.room import RoomType, RoomMemoryPolicy, Room


class TestRoomsIntegration:
    """Integration tests for the entire rooms system."""

    @pytest.fixture
    def test_db(self):
        """Create isolated test database."""
        import uuid
        import os
        from pathlib import Path
        
        # Get the original DB_PATH to restore later
        original_db_path = os.environ.get("DB_PATH")
        
        # Create temporary test database
        with tempfile.TemporaryDirectory() as tmpdir:
            test_db_path = Path(tmpdir) / f"test_rooms_{uuid.uuid4().hex}.db"
            os.environ["DB_PATH"] = str(test_db_path)
            
            # Import database initialization after setting DB_PATH
            from chatbot.db import init_db
            
            # Run async init_db
            asyncio.run(init_db())
            
            yield test_db_path
            
            # Restore original DB_PATH
            if original_db_path:
                os.environ["DB_PATH"] = original_db_path
            elif "DB_PATH" in os.environ:
                del os.environ["DB_PATH"]

    @pytest.mark.asyncio
    async def test_create_room_with_default_policy(self, test_db):
        """Test creating a room with default memory policy."""
        user_id = "test_user_123"
        room = await create_room(
            user_id=user_id,
            room_type=RoomType.ONE_ON_ONE_CHAT,
            display_name="Test General Room"
        )

        assert room.user_id == user_id
        assert room.room_type == RoomType.ONE_ON_ONE_CHAT
        assert room.display_name == "Test General Room"
        assert room.companion_id is None
        assert room.memory_policy.persist_to_durable is True
        assert room.memory_policy.scope == "room"
        assert room.memory_policy.max_context_turns == 50

    @pytest.mark.asyncio
    async def test_create_room_with_companion(self, test_db):
        """Test creating a one-on-one chat room with companion."""
        user_id = "user_1"
        companion_id = "bot_1"
        
        room = await create_room(
            user_id=user_id,
            room_type=RoomType.ONE_ON_ONE_CHAT,
            display_name="Private Chat",
            companion_id=companion_id
        )

        assert room.user_id == user_id
        assert room.companion_id == companion_id
        assert room.room_type == RoomType.ONE_ON_ONE_CHAT

    @pytest.mark.asyncio
    async def test_persist_through_session_restart(self, test_db):
        """Test that rooms persist across database session restarts."""
        user_id = "persistent_user"
        
        # Create room in first session
        original_room = await create_room(
            user_id=user_id,
            room_type=RoomType.PROJECT,
            display_name="Persistent Project"
        )
        
        room_id = original_room.room_id
        
        # Restart database (this simulates session restart by reusing same path)
        from chatbot.db import init_db
        await init_db()
        
        # Retrieve room in new session
        retrieved_room = await get_room(room_id)
        
        assert retrieved_room is not None
        assert retrieved_room.room_id == original_room.room_id
        assert retrieved_room.user_id == original_room.user_id
        assert retrieved_room.display_name == original_room.display_name
        assert retrieved_room.room_type == original_room.room_type

    @pytest.mark.asyncio
    async def test_get_nonexistent_room_returns_none(self, test_db):
        """Test that getting a non-existent room returns None."""
        fake_room_id = "nonexistent-room-id"
        retrieved_room = await get_room(fake_room_id)
        assert retrieved_room is None

    @pytest.mark.asyncio
    async def test_get_user_rooms_returns_only_user_rooms(self, test_db):
        """Test that get_user_rooms only returns rooms for the specified user."""
        # Create rooms for different users
        user1 = "alice"
        user2 = "bob"
        
        # Create rooms for alice
        alice_room1 = await create_room(user1, RoomType.ONE_ON_ONE_CHAT, "Alice Chat 1")
        alice_room2 = await create_room(user1, RoomType.ROLEPLAY_SCENE, "Alice RPG")
        
        # Create rooms for bob
        bob_room1 = await create_room(user2, RoomType.EMOTIONAL_SUPPORT, "Bob Support")
        
        # Get alice's rooms
        alice_rooms = await get_user_rooms(user1)
        alice_room_ids = {r.room_id for r in alice_rooms}
        
        assert len(alice_rooms) == 2
        assert alice_room1.room_id in alice_room_ids
        assert alice_room2.room_id in alice_room_ids
        assert bob_room1.room_id not in alice_room_ids

    @pytest.mark.asyncio
    async def test_update_room_memory_policy(self, test_db):
        """Test updating memory policy for existing room."""
        user_id = "test_user"
        
        # Create room with default policy
        original_room = await create_room(
            user_id=user_id,
            room_type=RoomType.EMOTIONAL_SUPPORT
        )
        
        # Create new policy with different settings
        new_policy = RoomMemoryPolicy(
            persist_to_durable=False,
            scope="global",
            max_context_turns=100,
            include_embeddings=False
        )
        
        # Update policy
        await update_room_memory_policy(original_room.room_id, new_policy)
        
        # Verify changes persisted
        updated_room = await get_room(original_room.room_id)
        assert updated_room is not None
        assert updated_room.memory_policy.persist_to_durable == False
        assert updated_room.memory_policy.scope == "global"
        assert updated_room.memory_policy.max_context_turns == 100
        assert updated_room.memory_policy.include_embeddings == False

    @pytest.mark.asyncio
    async def test_update_room_memory_policy_updates_only_policy(self, test_db):
        """Test that updating policy doesn't affect other room properties."""
        user_id = "test_user"
        
        room = await create_room(
            user_id=user_id,
            room_type=RoomType.ONE_ON_ONE_CHAT,
            display_name="Original Name"
        )
        
        # Store original values
        original_display_name = room.display_name
        original_room_type = room.room_type
        
        new_policy = RoomMemoryPolicy(
            persist_to_durable=False,
            scope="companions"
        )
        
        await update_room_memory_policy(room.room_id, new_policy)
        
        # Verify other properties unchanged
        updated_room = await get_room(room.room_id)
        assert updated_room.display_name == original_display_name
        assert updated_room.room_type == original_room_type

    @pytest.mark.asyncio
    async def test_room_type_variations(self, test_db):
        """Test creating rooms with different room types."""
        user_id = "multi_type_user"
        
        room_types = [
            RoomType.ONE_ON_ONE_CHAT,
            RoomType.ROLEPLAY_SCENE,
            RoomType.PROJECT,
            RoomType.EMOTIONAL_SUPPORT,
            RoomType.MEMORY_REVIEW
        ]
        
        created_rooms = []
        for room_type in room_types:
            room = await create_room(
                user_id=user_id,
                room_type=room_type,
                display_name=f"Test {room_type.value}"
            )
            created_rooms.append(room)
        
        # Verify all created rooms are retrievable
        user_rooms = await get_user_rooms(user_id)
        assert len(user_rooms) == len(room_types)
        
        retrievable_room_types = {r.room_type for r in user_rooms}
        assert retrievable_room_types == set(room_types)

    @pytest.mark.asyncio
    async def test_update_nonexistent_room_fails(self, test_db):
        """Test that updating policy for non-existent room raises ValueError."""
        fake_room_id = "fake-room-id"
        new_policy = RoomMemoryPolicy(
            persist_to_durable=True,
            scope="room",
            max_context_turns=75
        )
        
        with pytest.raises(ValueError, match="Room not found"):
            await update_room_memory_policy(fake_room_id, new_policy)

    @pytest.mark.asyncio
    async def test_user_room_ordering(self, test_db):
        """Test that get_user_rooms returns rooms in reverse chronological order."""
        user_id = "time_order_user"
        
        # Create rooms in sequence (should be reverse ordered)
        room1 = await create_room(user_id, RoomType.PROJECT, "Room 1")
        room2 = await create_room(user_id, RoomType.PROJECT, "Room 2")
        room3 = await create_room(user_id, RoomType.PROJECT, "Room 3")
        
        user_rooms = await get_user_rooms(user_id)
        
        assert len(user_rooms) == 3
        # Should be [room3, room2, room1] due to created_at DESC ordering
        room_ids = [r.room_id for r in user_rooms]
        assert room_ids.index(room3.room_id) < room_ids.index(room2.room_id)
        assert room_ids.index(room2.room_id) < room_ids.index(room1.room_id)

    @pytest.mark.asyncio
    async def test_empty_room_metadata(self, test_db):
        """Test that rooms are created with empty metadata."""
        user_id = "metadata_user"
        
        room = await create_room(
            user_id=user_id,
            room_type=RoomType.ONE_ON_ONE_CHAT,
            display_name="Test Metadata"
        )
        
        assert room.metadata == {}
        
        # Verify empty metadata persists
        retrieved = await get_room(room.room_id)
        assert retrieved.metadata == {}