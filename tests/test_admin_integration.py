import pytest
from chatbot.app import create_app

@pytest.fixture
def admin_client():
    app = create_app()
    app.config['TESTING'] = True
    app.config['ADMIN_KEY'] = 'test_admin_key'
    with app.test_client() as client:
        yield client

def test_admin_status(admin_client):
    """Verify admin status endpoint requires authentication"""
    # Unauthorized access
    response = admin_client.get('/admin/status')
    assert response.status_code == 401

    # Authorized access
    response = admin_client.get(
        '/admin/status',
        headers={'X-ADMIN-KEY': 'test_admin_key'}
    )
    assert response.status_code == 200
    assert 'version' in response.json
    assert 'active_sessions' in response.json

def test_admin_model_reload(admin_client):
    """Verify model reload triggers backend refresh"""
    response = admin_client.post(
        '/admin/reload-model',
        headers={'X-ADMIN-KEY': 'test_admin_key'},
        json={'model': 'openrouter/qwen/qwen3-235b'}
    )
    assert response.status_code == 200
    assert response.json['status'] == 'reloading'