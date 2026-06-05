import pytest
from chatbot import create_app

@pytest.fixture
def app():
    return create_app(testing=True)

@pytest.fixture
def client(app):
    return app.test_client()

class TestRoomMemoryIsolation:
    def test_room_scoped_memory(self, client):
        # Create message in room1
        room1_response = client.post('/api/rooms/room1/messages', json={'content': 'Remember user: alice'})
        assert room1_response.status_code == 200
        
        # Check room2 doesn't have room1's memory
        room2_response = client.get('/api/rooms/room2/state')
        assert 'alice' not in room2_response.get_json().get('context', '')
        
        # Verify room1 maintains its own state
        room1_check = client.get('/api/rooms/room1/state')
        assert 'alice' in room1_check.get_json().get('context', '')