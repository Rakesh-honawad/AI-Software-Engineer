from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from api.main import app
from auth.dependencies import AuthenticatedUser, get_current_user
from github_integration import repository_routes as github_repo_routes


USER_A = AuthenticatedUser(
    id="github-index-user-a",
    email="a@example.com",
    claims={},
)


@pytest.fixture
def client():
    app.dependency_overrides[get_current_user] = lambda: USER_A
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.pop(get_current_user, None)


def configure_successful_index(monkeypatch, tmp_path):
    captured = {}

    monkeypatch.setattr(
        github_repo_routes.store,
        "get_installation",
        lambda user_id, installation_id: {
            "installation_id": installation_id,
            "account_login": "test-account",
        } if user_id == USER_A.id else None,
    )
    monkeypatch.setattr(
        github_repo_routes,
        "_verified_installation",
        lambda installation_id: {
            "id": installation_id,
            "app_id": "12345",
            "account_login": "test-account",
        },
    )
    monkeypatch.setattr(
        github_repo_routes,
        "list_installation_repositories",
        lambda installation_id: [
            {
                "id": 456789,
                "name": "demo-repo",
                "full_name": "test-account/demo-repo",
                "owner_login": "test-account",
                "private": True,
                "default_branch": "main",
                "html_url": "https://github.com/test-account/demo-repo",
            }
        ],
    )

    def fake_create_token(installation_id, repository_ids=None):
        captured["token_installation_id"] = installation_id
        captured["repository_ids"] = repository_ids
        return "temporary-test-installation-token"

    monkeypatch.setattr(
        github_repo_routes,
        "create_installation_token",
        fake_create_token,
    )

    class FakeConnector:
        def __init__(self, owner, repo, token=None):
            captured["connector_owner"] = owner
            captured["connector_repo"] = repo
            captured["connector_token"] = token

        def fetch(self, target_dir):
            captured["clone_target"] = target_dir
            return SimpleNamespace(
                local_path=str(tmp_path / "indexed-repository"),
                remote_url="https://github.com/test-account/demo-repo",
                default_branch="main",
                commit="abc123",
                is_private=True,
            )

    monkeypatch.setattr(
        github_repo_routes,
        "GitHubConnector",
        FakeConnector,
    )
    monkeypatch.setattr(
        github_repo_routes,
        "_user_storage_dir",
        lambda repository_id, user_id: tmp_path / "storage",
    )

    def fake_begin_indexing(**kwargs):
        captured["index_kwargs"] = kwargs
        return SimpleNamespace(
            repository_id=kwargs["repository_id"],
            name=kwargs["repository_name"],
            index_status=SimpleNamespace(value="Indexing"),
            last_indexed_commit="",
            graph_hash="",
            updated_at=datetime(2026, 11, 5),
        )

    monkeypatch.setattr(
        github_repo_routes,
        "begin_indexing",
        fake_begin_indexing,
    )
    monkeypatch.setattr(
        github_repo_routes,
        "run_indexing",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        github_repo_routes.store,
        "audit_event",
        lambda *args, **kwargs: None,
    )

    return captured


def test_github_app_index_requires_authentication():
    app.dependency_overrides.pop(get_current_user, None)

    with TestClient(app) as test_client:
        response = test_client.post(
            "/repositories/index/github-app",
            json={
                "repository_id": "demo-repo",
                "installation_id": 9876,
                "github_repository_id": 456789,
            },
        )

    assert response.status_code == 401


def test_github_app_index_rejects_another_users_installation(
    client,
    monkeypatch,
):
    monkeypatch.setattr(
        github_repo_routes.store,
        "get_installation",
        lambda user_id, installation_id: None,
    )

    response = client.post(
        "/repositories/index/github-app",
        json={
            "repository_id": "demo-repo",
            "installation_id": 9876,
            "github_repository_id": 456789,
        },
    )

    assert response.status_code == 404


def test_github_app_index_rejects_repository_outside_installation(
    client,
    monkeypatch,
):
    monkeypatch.setattr(
        github_repo_routes.store,
        "get_installation",
        lambda user_id, installation_id: {"installation_id": installation_id},
    )
    monkeypatch.setattr(
        github_repo_routes,
        "_verified_installation",
        lambda installation_id: {"id": installation_id, "app_id": "12345"},
    )
    monkeypatch.setattr(
        github_repo_routes,
        "list_installation_repositories",
        lambda installation_id: [],
    )

    token_calls = []
    monkeypatch.setattr(
        github_repo_routes,
        "create_installation_token",
        lambda *args, **kwargs: token_calls.append(args),
    )

    response = client.post(
        "/repositories/index/github-app",
        json={
            "repository_id": "demo-repo",
            "installation_id": 9876,
            "github_repository_id": 456789,
        },
    )

    assert response.status_code == 403
    assert token_calls == []


def test_github_app_index_uses_repository_scoped_token_and_starts_indexing(
    client,
    monkeypatch,
    tmp_path,
):
    captured = configure_successful_index(monkeypatch, tmp_path)

    response = client.post(
        "/repositories/index/github-app",
        json={
            "repository_id": "demo-repo",
            "installation_id": 9876,
            "github_repository_id": 456789,
        },
    )

    assert response.status_code == 202
    assert response.json()["repository_id"] == "demo-repo"
    assert response.json()["name"] == "demo-repo"
    assert response.json()["index_status"] == "Indexing"

    # The installation token must be restricted to the selected repository.
    assert captured["token_installation_id"] == 9876
    assert captured["repository_ids"] == [456789]

    # The connector receives GitHub-derived identity, not request-supplied names.
    assert captured["connector_owner"] == "test-account"
    assert captured["connector_repo"] == "demo-repo"
    assert captured["connector_token"] == "temporary-test-installation-token"

    # The indexing record carries the source metadata needed by later PR work.
    kwargs = captured["index_kwargs"]
    assert kwargs["owner_id"] == USER_A.id
    assert kwargs["source_type"] == "github_app"
    assert kwargs["github_owner"] == "test-account"
    assert kwargs["github_repo"] == "demo-repo"
    assert kwargs["github_repository_id"] == 456789
    assert kwargs["github_default_branch"] == "main"
    assert kwargs["github_installation_record_id"] == 9876

def test_github_app_index_rejects_invalid_repository_ids(client):
    response = client.post(
        "/repositories/index/github-app",
        json={
            "repository_id": "demo-repo",
            "installation_id": 9876,
            "github_repository_id": -1,
        },
    )

    assert response.status_code == 422
