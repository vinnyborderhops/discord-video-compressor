from pathlib import Path

from compressor.config import ConfigurationStore, get_config_directory
from compressor.encoders import ENCODERS


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
