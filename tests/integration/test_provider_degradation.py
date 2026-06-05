import asyncio
import pytest
import httpx
from unittest.mock import patch, AsyncMock, MagicMock
from chatbot.api.app import create_app as create_app
from chatbot.api.routers.chat import ChatRequest
from chatbot.providers.base import ProviderRegistry, Provider
from chatbot.providers.exceptions import ProviderError, ProviderUnavailableError


class TestProviderGracefulDegradation:
    """Test suite for provider graceful degradation behavior."""

    @pytest.fixture
    def app(self):
        """Create FastAPI app for testing."""
        return create_app()

    @pytest.fixture
    def client(self, app):
        """Create test client."""
        return httpx.AsyncClient(app=app, base_url="http://test")

    @pytest.mark.asyncio
    async def test_single_provider_failure_graceful_degradation(self, client):
        """Test that a single provider failure doesn't crash the system."""
        # Mock provider to simulate failure
        with patch("chatbot.providers.provider_registry.ProviderRegistry.get_provider") as mock_get:
            mock_provider = AsyncMock()
            mock_provider.generate_response.side_effect = ProviderUnavailableError("Test provider failure")
            mock_get.return_value = mock_provider

            request_data = {"message": "Hello", "user_id": "test-user"}
            
            # Should return a graceful degraded response instead of crashing
            response = await client.post("/chat", json=request_data)
            
            # Should not crash, but return a meaningful response
            assert response.status_code == 503
            assert "provider unavailable" in response.json()["detail"].lower()

    @pytest.mark.asyncio  
    async def test_provider_fallback_chain(self, client):
        """Test fallback to backup providers when primary fails."""
        with patch("chatbot.providers.provider_registry.ProviderRegistry.get_fallback_providers") as mock_get_fallbacks:
            # Primary provider fails
            primary = AsyncMock()
            primary.generate_response.side_effect = ProviderUnavailableError("Primary down")
            
            # Fallback provider succeeds
            fallback = AsyncMock()
            fallback.generate_response.return_value = "Fallback response"
            
            mock_get_fallbacks.return_value = [primary, fallback]
            
            request_data = {"message": "Hello", "user_id": "test-user"}
            response = await client.post("/chat", json=request_data)
            
            # Should get successful response from fallback
            assert response.status_code == 200
            data = response.json()
            assert "Fallback response" in data["response"]

    @pytest.mark.asyncio
    async def test_all_providers_down_graceful_message(self, client):
        """Test system behavior when all providers are down."""
        with patch("chatbot.providers.provider_registry.ProviderRegistry.get_all_providers") as mock_get_all:
            # All providers fail
            provider1 = AsyncMock()
            provider2 = AsyncMock()
            provider1.generate_response.side_effect = ProviderUnavailableError("Provider1 down")
            provider2.generate_response.side_effect = ProviderUnavailableError("Provider2 down")
            
            mock_get_all.return_value = [provider1, provider2]
            
            request_data = {"message": "Hello", "user_id": "test-user"}
            response = await client.post("/chat", json=request_data)
            
            # Should return meaningful user-friendly message
            assert response.status_code == 503
            response_data = response.json()
            assert "service temporarily unavailable" in response_data["detail"].lower()

    @pytest.mark.asyncio
    async def test_partial_provider_degradation_performance_impact(self, client):
        """Test that degradation affects performance gracefully."""
        with patch("chatbot.providers.provider_registry.ProviderRegistry.get_provider") as mock_get, \
             patch("asyncio.sleep") as mock_sleep:
            
            # Simulate slow provider (degraded performance)
            mock_provider = AsyncMock()
            mock_provider.generate_response.return_value = "Slow response"
            
            # Add a delay to simulate degraded performance
            async def delayed_response(*args, **kwargs):
                await asyncio.sleep(5)
                return "Delayed response"
            
            mock_provider.generate_response = delayed_response
            mock_get.return_value = mock_provider
            
            request_data = {"message": "Hello", "user_id": "test-user"}
            
            # Should handle timeout gracefully
            try:
                response = await client.post("/chat", json=request_data, timeout=1)
                assert False, "Should have timed out"  # noqa: B011
            except httpx.TimeoutException:
                # Expected behavior for timeout
                pass

    @pytest.mark.asyncio
    async def test_monitoring_health_check_endpoint(self):
        """Test that health monitoring reports provider status."""
        app = create_app()
        
        async with httpx.AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get("/health/providers")
            
            assert response.status_code == 200
            health_data = response.json()
            
            # Verify health check structure
            assert "providers" in health_data
            assert "status" in health_data
            assert health_data["status"] in ["healthy", "degraded", "unavailable"]
            
            if len(health_data["providers"]) > 0:
                provider = health_data["providers"][0]
                assert "name" in provider
                assert "status" in provider
                assert "last_check" in provider

    @pytest.mark.asyncio
    async def test_circuit_breaker_behavior(self, client):
        """Test circuit breaker prevents cascade failures."""
        with patch("chatbot.providers.circuit_breaker.CircuitBreaker.is_open") as mock_is_open:
            # Circuit breaker is open due to repeated failures
            mock_is_open.return_value = True
            
            request_data = {"message": "Hello", "user_id": "test-user"}
            response = await client.post("/chat", json=request_data)
            
            # Should fail fast with circuit breaker open
            assert response.status_code == 503
            assert "circuit breaker" in response.json()["detail"].lower()

    def test_provider_health_monitoring(self):
        """Test provider health monitoring detects degradation."""
        from chatbot.providers.health_monitor import ProviderHealthMonitor
        
        monitor = ProviderHealthMonitor()
        
        # Test healthy provider
        healthy_provider = MagicMock()
        healthy_provider.health_check.return_value = True
        assert monitor.check_provider_health(healthy_provider) == "healthy"
        
        # Test degraded provider
        degraded_provider = MagicMock()
        degraded_provider.health_check.return_value = False
        assert monitor.check_provider_health(degraded_provider) == "degraded"

    @pytest.mark.asyncio
    async def test_provider_retry_mechanism(self):
        """Test retry mechanism handles transient failures."""
        from chatbot.providers.retry_handler import RetryProvider
        
        mock_provider = AsyncMock()
        
        # First call fails, second succeeds
        mock_provider.generate_response.side_effect = [
            ProviderUnavailableError("Transient failure"), 
            "Success after retry"
        ]
        
        retry_provider = RetryProvider(mock_provider, max_retries=2)
        result = await retry_provider.generate_response("test message")
        
        # Should succeed on retry
        assert result == "Success after retry"
        assert mock_provider.generate_response.call_count == 2