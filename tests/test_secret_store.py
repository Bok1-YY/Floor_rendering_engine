import json

import pytest

from Floor_engine_server import config, secret_store


class FakeBackend:
    priority = 5


class FakeKeyring:
    def __init__(self):
        self.values = {}
        self.fail_set = False

    def get_password(self, service, account):
        return self.values.get((service, account))

    def set_password(self, service, account, value):
        if self.fail_set:
            raise RuntimeError("injected keyring failure")
        self.values[(service, account)] = value

    def delete_password(self, service, account):
        self.values.pop((service, account), None)


@pytest.fixture
def fake_keyring(monkeypatch):
    fake = FakeKeyring()
    monkeypatch.setattr(secret_store, "_keyring_module", lambda: (fake, FakeBackend()))
    secret_store.clear_cache()
    return fake


def test_environment_secret_has_priority(monkeypatch, fake_keyring):
    fake_keyring.set_password(secret_store.SERVICE_NAME, "gemini_api_key", "stored")
    monkeypatch.setenv("GEMINI_API_KEY", "environment")
    resolved = secret_store.resolve_secret("gemini_api_key", "legacy")
    assert resolved.value == "environment"
    assert resolved.source == "environment"


def test_keyring_set_read_and_delete(fake_keyring):
    secret_store.set_secret("fal_api_key", "secret")
    secret_store.clear_cache()
    resolved = secret_store.resolve_secret("fal_api_key")
    assert (resolved.value, resolved.source) == ("secret", "keyring")
    secret_store.delete_secret("fal_api_key")
    secret_store.clear_cache()
    assert secret_store.resolve_secret("fal_api_key").source == "missing"


def test_save_config_never_serializes_runtime_secrets(tmp_path, monkeypatch):
    target = tmp_path / "engine_config.json"
    monkeypatch.setattr(config, "CONFIG_FILE", str(target))
    assert config.save_config({"gemini_api_key": "do-not-write", "proxy": "p"}) is True
    raw = json.loads(target.read_text(encoding="utf-8"))
    assert raw == {"proxy": "p"}
    assert "do-not-write" not in target.read_text(encoding="utf-8")


def test_plaintext_migration_verifies_then_scrubs(tmp_path, monkeypatch, fake_keyring):
    target = tmp_path / "engine_config.json"
    target.write_text(json.dumps({
        "gemini_api_key": "g",
        "fal_api_key": "f",
        "proxy": "p",
    }), encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_FILE", str(target))
    monkeypatch.setattr(config, "set_secret", secret_store.set_secret)

    assert config.migrate_plaintext_secrets() is True
    assert json.loads(target.read_text(encoding="utf-8")) == {"proxy": "p"}
    assert fake_keyring.get_password(secret_store.SERVICE_NAME, "gemini_api_key") == "g"
    assert fake_keyring.get_password(secret_store.SERVICE_NAME, "fal_api_key") == "f"


def test_plaintext_migration_failure_preserves_file(tmp_path, monkeypatch, fake_keyring):
    target = tmp_path / "engine_config.json"
    original = json.dumps({"gemini_api_key": "g", "proxy": "p"})
    target.write_text(original, encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_FILE", str(target))
    monkeypatch.setattr(config, "set_secret", secret_store.set_secret)
    fake_keyring.fail_set = True

    assert config.migrate_plaintext_secrets() is False
    assert target.read_text(encoding="utf-8") == original


def test_update_config_routes_secret_away_from_json(tmp_path, monkeypatch, fake_keyring):
    target = tmp_path / "engine_config.json"
    target.write_text('{"proxy":"old"}', encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_FILE", str(target))
    monkeypatch.setattr(config, "set_secret", secret_store.set_secret)

    assert config.update_config({"deepseek_api_key": "d", "proxy": "new"}) is True
    assert json.loads(target.read_text(encoding="utf-8")) == {"proxy": "new"}
    assert fake_keyring.get_password(secret_store.SERVICE_NAME, "deepseek_api_key") == "d"
