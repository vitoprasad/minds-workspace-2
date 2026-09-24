import pytest
from flask import Flask
from flask.testing import FlaskClient

from chat_bridge.api import register_api
from chat_bridge.auth import COOKIE_NAME

_TOKEN = "unit-test-token"


@pytest.fixture
def client() -> FlaskClient:
    app = Flask("chat_bridge_test", static_folder=None)
    register_api(app, _TOKEN)
    return app.test_client()


def test_agents_without_token_is_401(client: FlaskClient) -> None:
    response = client.get("/api/agents")
    assert response.status_code == 401
    assert response.get_json()["error"]


def test_agents_with_wrong_bearer_is_401(client: FlaskClient) -> None:
    response = client.get("/api/agents", headers={"Authorization": "Bearer nope"})
    assert response.status_code == 401


def test_login_rejects_bad_token(client: FlaskClient) -> None:
    response = client.post("/api/login", json={"token": "wrong"})
    assert response.status_code == 401


def test_login_accepts_token_and_sets_cookie(client: FlaskClient) -> None:
    response = client.post("/api/login", json={"token": _TOKEN})
    assert response.status_code == 200
    set_cookie = response.headers.get("Set-Cookie", "")
    assert COOKIE_NAME in set_cookie
    assert "HttpOnly" in set_cookie


def test_cookie_from_login_authorizes_later_requests(client: FlaskClient) -> None:
    client.post("/api/login", json={"token": _TOKEN})
    # The test client retains the cookie; a message with an empty body should now
    # pass auth and fail validation (400), not auth (401).
    response = client.post("/api/agents/anything/message", json={"message": "   "})
    assert response.status_code == 400


def test_message_requires_nonempty_message(client: FlaskClient) -> None:
    response = client.post(
        "/api/agents/anything/message",
        json={"message": ""},
        headers={"Authorization": f"Bearer {_TOKEN}"},
    )
    assert response.status_code == 400


def test_logout_clears_cookie(client: FlaskClient) -> None:
    response = client.post("/api/logout", headers={"Authorization": f"Bearer {_TOKEN}"})
    assert response.status_code == 200
