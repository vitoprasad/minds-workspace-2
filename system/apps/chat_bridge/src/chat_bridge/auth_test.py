from pathlib import Path

from chat_bridge.auth import COOKIE_NAME
from chat_bridge.auth import TOKEN_ENV_VAR
from chat_bridge.auth import _read_token_file
from chat_bridge.auth import _write_token_file
from chat_bridge.auth import extract_presented_token
from chat_bridge.auth import is_authorized
from chat_bridge.auth import load_or_create_token


def test_write_then_read_token_roundtrips(tmp_path: Path) -> None:
    path = tmp_path / "chat_bridge.env"
    _write_token_file(path, "abc123")
    assert _read_token_file(path) == "abc123"


def test_written_token_file_is_owner_only(tmp_path: Path) -> None:
    path = tmp_path / "chat_bridge.env"
    _write_token_file(path, "secret")
    assert (path.stat().st_mode & 0o777) == 0o600


def test_read_missing_token_file_returns_none(tmp_path: Path) -> None:
    assert _read_token_file(tmp_path / "absent.env") is None


def test_load_token_prefers_environment(monkeypatch) -> None:
    monkeypatch.setenv(TOKEN_ENV_VAR, "from-env")
    assert load_or_create_token() == "from-env"


def test_extract_token_prefers_bearer_header() -> None:
    headers = {"Authorization": "Bearer tok-a", "X-Chat-Bridge-Token": "tok-b"}
    assert extract_presented_token(headers, {COOKIE_NAME: "tok-c"}) == "tok-a"


def test_extract_token_falls_back_to_custom_header_then_cookie() -> None:
    assert extract_presented_token({"X-Chat-Bridge-Token": "tok-b"}, {}) == "tok-b"
    assert extract_presented_token({}, {COOKIE_NAME: "tok-c"}) == "tok-c"


def test_extract_token_returns_none_when_absent() -> None:
    assert extract_presented_token({}, {}) is None
    assert extract_presented_token({"Authorization": "Bearer "}, {}) is None


def test_is_authorized_matches_only_exact_token() -> None:
    assert is_authorized("right", "right") is True
    assert is_authorized("wrong", "right") is False
    assert is_authorized("", "right") is False
    assert is_authorized(None, "right") is False
