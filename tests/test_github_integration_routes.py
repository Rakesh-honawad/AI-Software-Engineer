from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from api.main import app
from auth.dependencies import AuthenticatedUser, get_current_user
from github_integration import routes as github_routes


USER_A = AuthenticatedUser(
    id="user-a",
    email="a@example.com",
    claims={},
)

USER_B = AuthenticatedUser(
    id="user-b",
    email="b@example.com",
    claims={},
)

STATE = "test-state-" + ("x" * 40)


@pytest.fixture
def client():
    app.dependency_overrides[get_current_user] = lambda: USER_A

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.pop(get_current_user, None)


def fake_config():
    return SimpleNamespace(
        app_id="12345",
        app_slug="test-github-app",
        pending_ttl_minutes=10,
    )


def fake_installation():
    return {
        "id": 9876,
        "app_id": "12345",
        "account_login": "test-account",
        "account_type": "Organization",
    }


def test_protected_endpoint_requires_authentication():
    app.dependency_overrides.pop(get_current_user, None)

    with TestClient(app) as test_client:
        response = test_client.get("/auth/github/installations")

    assert response.status_code == 401


def test_begin_installation_creates_state_and_url(client, monkeypatch):
    captured = {}

    monkeypatch.setattr(github_routes, "load_config", fake_config)
    monkeypatch.setattr(github_routes.store, "init_db", lambda: None)

    def fake_create_state(user_id, state, expires_at):
        captured["user_id"] = user_id
        captured["state"] = state
        captured["expires_at"] = expires_at

    monkeypatch.setattr(
        github_routes.store,
        "create_oauth_state",
        fake_create_state,
    )
    monkeypatch.setattr(
        github_routes.store,
        "audit_event",
        lambda *args, **kwargs: None,
    )

    response = client.get("/auth/github/install")

    assert response.status_code == 200
    data = response.json()

    assert data["install_url"].startswith(
        "https://github.com/apps/test-github-app/installations/new?state="
    )
    assert captured["user_id"] == USER_A.id
    assert captured["state"] in data["install_url"]
    assert len(captured["state"]) >= 32
    assert "expires_at" in data


def test_callback_rejects_unsupported_setup_action(client, monkeypatch):
    def unexpected_consume(*args, **kwargs):
        raise AssertionError("State must not be consumed for unsupported action")

    monkeypatch.setattr(
        github_routes.store,
        "consume_oauth_state",
        unexpected_consume,
    )

    response = client.get(
        "/auth/github/callback",
        params={
            "installation_id": 9876,
            "setup_action": "delete",
            "state": STATE,
        },
    )

    assert response.status_code == 400


def test_callback_rejects_invalid_or_replayed_state(client, monkeypatch):
    monkeypatch.setattr(
        github_routes.store,
        "consume_oauth_state",
        lambda state: None,
    )

    response = client.get(
        "/auth/github/callback",
        params={
            "installation_id": 9876,
            "setup_action": "install",
            "state": STATE,
        },
    )

    assert response.status_code == 400


def test_callback_saves_verified_installation_as_pending(client, monkeypatch):
    captured = {}

    monkeypatch.setattr(
        github_routes.store,
        "consume_oauth_state",
        lambda state: USER_A.id,
    )
    monkeypatch.setattr(
        github_routes,
        "_verified_installation",
        lambda installation_id: fake_installation(),
    )
    monkeypatch.setattr(github_routes, "load_config", fake_config)

    def fake_save_pending(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(
        github_routes.store,
        "save_pending_installation",
        fake_save_pending,
    )
    monkeypatch.setattr(
        github_routes.store,
        "audit_event",
        lambda *args, **kwargs: None,
    )

    response = client.get(
        "/auth/github/callback",
        params={
            "installation_id": 9876,
            "setup_action": "install",
            "state": STATE,
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "pending_confirmation"
    assert response.json()["confirmation_required"] is True
    assert response.json()["account_login"] == "test-account"

    assert captured["user_id"] == USER_A.id
    assert captured["installation_id"] == 9876
    assert captured["state"] == STATE


def test_confirmation_rejects_another_users_pending_installation(
    client, monkeypatch
):
    verification_called = False

    monkeypatch.setattr(
        github_routes.store,
        "get_pending_installation",
        lambda user_id, state: None,
    )

    def unexpected_verification(installation_id):
        nonlocal verification_called
        verification_called = True
        raise AssertionError("Must not verify an installation not owned by user")

    monkeypatch.setattr(
        github_routes,
        "_verified_installation",
        unexpected_verification,
    )

    response = client.post(
        "/auth/github/confirm",
        json={"state": STATE},
    )

    assert response.status_code == 404
    assert verification_called is False


def test_confirmation_persists_installation_for_current_user(
    client, monkeypatch
):
    captured = {}

    monkeypatch.setattr(
        github_routes.store,
        "get_pending_installation",
        lambda user_id, state: (
            9876 if user_id == USER_A.id and state == STATE else None
        ),
    )
    monkeypatch.setattr(
        github_routes,
        "_verified_installation",
        lambda installation_id: fake_installation(),
    )

    def fake_save(**kwargs):
        captured["saved"] = kwargs

    def fake_delete(user_id, state):
        captured["deleted"] = (user_id, state)

    monkeypatch.setattr(
        github_routes.store,
        "save_installation",
        fake_save,
    )
    monkeypatch.setattr(
        github_routes.store,
        "delete_pending_installation",
        fake_delete,
    )
    monkeypatch.setattr(
        github_routes.store,
        "audit_event",
        lambda *args, **kwargs: None,
    )

    response = client.post(
        "/auth/github/confirm",
        json={"state": STATE},
    )

    assert response.status_code == 200
    assert response.json()["connected"] is True
    assert response.json()["installation_id"] == 9876

    assert captured["saved"]["user_id"] == USER_A.id
    assert captured["saved"]["installation_id"] == 9876
    assert captured["deleted"] == (USER_A.id, STATE)


def test_repository_listing_rejects_another_users_installation(
    client, monkeypatch
):
    verification_called = False

    monkeypatch.setattr(
        github_routes.store,
        "get_installation",
        lambda user_id, installation_id: None,
    )

    def unexpected_verification(installation_id):
        nonlocal verification_called
        verification_called = True
        raise AssertionError("Must not access another user's installation")

    monkeypatch.setattr(
        github_routes,
        "_verified_installation",
        unexpected_verification,
    )

    response = client.get("/auth/github/installations/9876/repositories")

    assert response.status_code == 404
    assert verification_called is False


def test_repository_listing_returns_repositories_for_owner(
    client, monkeypatch
):
    monkeypatch.setattr(
        github_routes.store,
        "get_installation",
        lambda user_id, installation_id: (
            {"installation_id": installation_id}
            if user_id == USER_A.id
            else None
        ),
    )
    monkeypatch.setattr(
        github_routes,
        "_verified_installation",
        lambda installation_id: fake_installation(),
    )
    monkeypatch.setattr(
        github_routes,
        "list_installation_repositories",
        lambda installation_id: [
            {
                "id": 111,
                "name": "demo-repository",
                "full_name": "test-account/demo-repository",
                "private": True,
            }
        ],
    )
    monkeypatch.setattr(
        github_routes.store,
        "audit_event",
        lambda *args, **kwargs: None,
    )

    response = client.get("/auth/github/installations/9876/repositories")

    assert response.status_code == 200
    repositories = response.json()["repositories"]
    assert len(repositories) == 1
    assert repositories[0]["full_name"] == "test-account/demo-repository"


def test_disconnect_cannot_delete_another_users_installation(
    client, monkeypatch
):
    monkeypatch.setattr(
        github_routes.store,
        "delete_installation",
        lambda user_id, installation_id: False,
    )

    response = client.delete("/auth/github/installations/9876")

    assert response.status_code == 404


def test_disconnect_removes_current_users_installation(client, monkeypatch):
    captured = {}

    def fake_delete(user_id, installation_id):
        captured["user_id"] = user_id
        captured["installation_id"] = installation_id
        return True

    monkeypatch.setattr(
        github_routes.store,
        "delete_installation",
        fake_delete,
    )
    monkeypatch.setattr(
        github_routes.store,
        "audit_event",
        lambda *args, **kwargs: None,
    )

    response = client.delete("/auth/github/installations/9876")

    assert response.status_code == 200
    assert response.json()["disconnected"] is True
    assert captured == {
        "user_id": USER_A.id,
        "installation_id": 9876,
    }
