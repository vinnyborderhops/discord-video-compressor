import json
from pathlib import Path

import pytest

from compressor.config import ConfigurationStore, get_config_directory
from compressor.encoders import ENCODERS
from compressor.errors import ConfigurationError


def test_platform_specific_directories():
    home = Path("/home/tester")

    assert get_config_directory(
        platform_name="win32",
        environ={"LOCALAPPDATA": "C:/Users/tester/AppData/Local"},
        home=home,
    ) == Path("C:/Users/tester/AppData/Local/DiscordVideoCompressor")
    assert (
        get_config_directory(
            platform_name="darwin",
            environ={},
            home=home,
        )
        == home / "Library" / "Application Support" / "DiscordVideoCompressor"
    )
    assert (
        get_config_directory(
            platform_name="linux",
            environ={},
            home=home,
        )
        == home / ".config" / "discord-video-compressor"
    )


def test_cache_round_trip(tmp_path):
    store = ConfigurationStore(tmp_path)
    store.save_encoder_cache("nvidia", "h264_nvenc")

    record = store.load_encoder_cache(ENCODERS)

    assert record is not None
    assert record.preferred_encoder == "nvidia"
    assert record.encoder == "h264_nvenc"


def test_corrupt_and_mismatched_cache_are_misses(tmp_path):
    store = ConfigurationStore(tmp_path)
    store.path.write_text("not json", encoding="utf-8")
    assert store.load_encoder_cache(ENCODERS) is None

    store.path.write_text(
        '{"schema_version": 1, "preferred_encoder": "cpu", '
        '"encoder": "h264_nvenc", "validated_at": "2026-01-01T00:00:00+00:00"}',
        encoding="utf-8",
    )
    assert store.load_encoder_cache(ENCODERS) is None


def test_pathologically_nested_cache_is_a_miss(tmp_path, monkeypatch):
    store = ConfigurationStore(tmp_path)
    store.path.write_text("{}", encoding="utf-8")

    def raise_recursion_error(_value):
        raise RecursionError

    monkeypatch.setattr("compressor.config.json.loads", raise_recursion_error)

    assert store.load_encoder_cache(ENCODERS) is None


def test_settings_are_created_with_defaults_on_first_load(tmp_path):
    store = ConfigurationStore(tmp_path)

    settings = store.load_settings()

    assert store.settings_path.name == "settings.json"
    assert store.encoder_cache_path.name == "encoder-cache.json"
    assert settings.target_size_mb == 20.0
    assert settings.encoder == "auto"
    assert settings.output.suffix == "_compressed"
    assert settings.quality.auto_downscale is True
    assert settings.console.pause_on_exit is True
    assert json.loads(store.settings_path.read_text(encoding="utf-8")) == settings.to_dict()


def test_custom_settings_are_loaded_and_validated(tmp_path):
    store = ConfigurationStore(tmp_path)
    settings = store.reset_settings().to_dict()
    settings["target_size_mb"] = 50
    settings["encoder"] = "cpu"
    settings["output"] = {"directory": "D:/Compressed Videos", "suffix": "_discord"}
    settings["quality"] = {
        "auto_downscale": False,
        "target_bits_per_pixel": 0.1,
        "minimum_dimension": 160,
    }
    settings["audio"] = {
        "minimum_bitrate_kbps": 48,
        "maximum_bitrate_kbps": 64,
    }
    settings["console"] = {"verbose": True, "pause_on_exit": False}
    store.settings_path.write_text(json.dumps(settings), encoding="utf-8")

    loaded = store.load_settings()

    assert loaded.target_size_mb == 50.0
    assert loaded.encoder == "cpu"
    assert loaded.output.directory == "D:/Compressed Videos"
    assert loaded.output.suffix == "_discord"
    assert loaded.quality.auto_downscale is False
    assert loaded.audio.maximum_bitrate_kbps == 64.0
    assert loaded.console.verbose is True


def test_invalid_settings_raise_without_being_overwritten(tmp_path):
    store = ConfigurationStore(tmp_path)
    invalid = '{"schema_version": 1, "target_size_mb": -1}'
    store.settings_path.write_text(invalid, encoding="utf-8")

    with pytest.raises(ConfigurationError, match="Invalid settings"):
        store.load_settings()

    assert store.settings_path.read_text(encoding="utf-8") == invalid


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("schema_version",), True),
        (("output", "suffix"), "../escape"),
        (("quality", "minimum_dimension"), 1),
        (("audio", "minimum_bitrate_kbps"), 97),
    ],
)
def test_invalid_setting_values_are_rejected(tmp_path, path, value):
    store = ConfigurationStore(tmp_path)
    raw = store.reset_settings().to_dict()
    target = raw
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    store.settings_path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ConfigurationError, match="Invalid settings"):
        store.load_settings()


def test_reset_settings_recovers_a_corrupt_file(tmp_path):
    store = ConfigurationStore(tmp_path)
    store.settings_path.write_text("not json", encoding="utf-8")

    settings = store.reset_settings()

    assert json.loads(store.settings_path.read_text(encoding="utf-8")) == settings.to_dict()


def test_saving_encoder_cache_does_not_rewrite_settings(tmp_path):
    store = ConfigurationStore(tmp_path)
    store.reset_settings()
    before = store.settings_path.read_bytes()

    store.save_encoder_cache("nvidia", "h264_nvenc")

    assert store.settings_path.read_bytes() == before
    assert store.encoder_cache_path.exists()
