"""Synthetic credentials only; these tests never read the real backend .env."""

import pytest

from backend import config


@pytest.fixture
def credentials(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    (tmp_path / "backend").mkdir()
    return tmp_path / "backend" / ".env"


def test_saved_key_loads_on_each_start_without_changing_the_environment(credentials):
    credentials.write_text("OPENAI_API_KEY='synthetic-saved-key'\n", encoding="utf-8")
    assert config.read_openai_key() == "synthetic-saved-key"
    assert config.read_openai_key() == "synthetic-saved-key"
    assert "OPENAI_API_KEY" not in config.os.environ
    settings = config.Settings(data_dir=credentials.parent, ffmpeg="unused", ffprobe="unused")
    assert settings.openai_api_key == "synthetic-saved-key"
    assert "synthetic-saved-key" not in repr(settings)


@pytest.mark.parametrize("value,expected", [(" environment-key ", "environment-key"), ("", "")])
def test_process_environment_takes_precedence(credentials, monkeypatch, value, expected):
    credentials.write_text("OPENAI_API_KEY=synthetic-saved-key\n", encoding="utf-8")
    monkeypatch.setenv("OPENAI_API_KEY", value)
    assert config.read_openai_key() == expected


def test_absent_key_keeps_manual_clipping_available(credentials):
    assert config.read_openai_key() == ""
    credentials.write_text("# No remote analysis configured\n", encoding="utf-8")
    assert config.read_openai_key() == ""


def test_file_values_are_literal_and_backend_path_is_independent_of_cwd(
    credentials, monkeypatch, tmp_path
):
    credentials.write_text("OPENAI_API_KEY='${LITERAL_TEST_VALUE}'\n", encoding="utf-8")
    monkeypatch.setenv("LITERAL_TEST_VALUE", "must-not-expand")
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.chdir(other)
    assert config.read_openai_key() == "${LITERAL_TEST_VALUE}"
