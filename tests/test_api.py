"""
Tests for API endpoints.
"""

import pytest


class TestHealthEndpoint:
    """Tests for the health check endpoint."""

    def test_health_check(self, client):
        """Test that health check returns OK."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "version" in data


class TestRootEndpoint:
    """Tests for the root endpoint."""

    def test_root(self, client):
        """Test that root returns API info."""
        response = client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert "name" in data
        assert "version" in data


class TestAccountsEndpoints:
    """Tests for the accounts endpoints."""

    def test_list_accounts_empty(self, client):
        """Test listing accounts when none exist."""
        response = client.get("/api/accounts")
        assert response.status_code == 200
        assert response.json() == []

    def test_create_account(self, client):
        """Test creating a new account."""
        response = client.post(
            "/api/accounts",
            json={
                "username": "testuser",
                "api_token": "test_token_123",
                "is_default": True,
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["username"] == "testuser"
        assert data["is_default"] is True
        assert "id" in data

    def test_create_duplicate_account(self, client):
        """Test that duplicate usernames are rejected."""
        # Create first account
        client.post(
            "/api/accounts",
            json={"username": "testuser", "api_token": "token1"},
        )
        # Try to create duplicate
        response = client.post(
            "/api/accounts",
            json={"username": "testuser", "api_token": "token2"},
        )
        assert response.status_code == 409

    def test_get_account(self, client):
        """Test getting a specific account."""
        # Create account
        create_response = client.post(
            "/api/accounts",
            json={"username": "testuser", "api_token": "token"},
        )
        account_id = create_response.json()["id"]

        # Get account
        response = client.get(f"/api/accounts/{account_id}")
        assert response.status_code == 200
        assert response.json()["username"] == "testuser"

    def test_get_nonexistent_account(self, client):
        """Test getting a nonexistent account."""
        response = client.get("/api/accounts/nonexistent-id")
        assert response.status_code == 404

    def test_delete_account(self, client):
        """Test deleting an account."""
        # Create account
        create_response = client.post(
            "/api/accounts",
            json={"username": "testuser", "api_token": "token"},
        )
        account_id = create_response.json()["id"]

        # Delete account
        response = client.delete(f"/api/accounts/{account_id}")
        assert response.status_code == 204

        # Verify deletion
        response = client.get(f"/api/accounts/{account_id}")
        assert response.status_code == 404


class TestGamesEndpoints:
    """Tests for the games endpoints."""

    def test_list_games_empty(self, client):
        """Test listing games when none exist."""
        response = client.get("/api/games")
        assert response.status_code == 200
        data = response.json()
        assert data["games"] == []
        assert data["total"] == 0


class TestInstancesEndpoints:
    """Tests for the instances endpoints."""

    def test_create_instance(self, client):
        """Test creating a new instance."""
        response = client.post(
            "/api/instances",
            json={"name": "Test Instance", "type": "chess"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "Test Instance"
        assert data["instance_type"] == "chess"
        assert data["current_screen"] == "setup"

    def test_list_instances(self, client):
        """Test listing instances."""
        # Create instance
        client.post(
            "/api/instances",
            json={"name": "Test Instance", "type": "chess"},
        )

        response = client.get("/api/instances")
        assert response.status_code == 200
        assert len(response.json()) == 1
