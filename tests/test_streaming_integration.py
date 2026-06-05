import pytest
from chatbot.app import create_app

@pytest.fixture
def client():
    app = create_app()
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client

def test_streaming_response(client):
    """Verify streaming endpoint emits tokens incrementally"""
    response = client.post('/chat', json={'message': 'Count to 5'}, stream=True)
    assert response.status_code == 200
    
    chunks = []
    for chunk in response.iter_content(chunk_size=None):
        if chunk:
            chunks.append(chunk.decode('utf-8').strip())
    
    # Verify streaming behavior (at least 3 chunks for '1 2 3 4 5')
    assert len(chunks) >= 3
    full_response = ''.join(chunks)
    assert '1' in full_response and '5' in full_response