import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from compressor import cli
from compressor.config import ConfigurationStore
from compressor.models import EncoderSelection


def test_main_uses_process_arguments_when_argv_is_omitted(monkeypatch):
    input_path = r"C:\Videos\replay with spaces.mp4"
    captured = []

    class ParsingStopped(Exception):
        pass

    class RecordingParser:
        def parse_args(self, argv):
            captured.extend(argv)
            raise ParsingStopped

    monkeypatch.setattr(sys, "argv", ["DiscordVideoCompressor.exe", input_path])
    monkeypatch.setattr(cli, "build_parser", RecordingParser)

    with pytest.raises(ParsingStopped):
        cli.main()

    assert captured == [input_path]


def test_show_config_creates_defaults_without_resolving_ffmpeg(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
):
    store = ConfigurationStore(tmp_path)
    monkeypatch.setattr(cli, "ConfigurationStore", lambda: store)
    monkeypatch.setattr(
        cli,
        "resolve_ffmpeg_tools",
        lambda: pytest.fail("config commands must not resolve FFmpeg"),
    )

    assert cli.main(["--show-config"]) == 0

    assert store.settings_path.exists()
    assert str(store.settings_path) in capsys.readouterr().out


def test_reset_config_recovers_corrupt_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    store = ConfigurationStore(tmp_path)
    store.settings_path.write_text("invalid", encoding="utf-8")
    monkeypatch.setattr(cli, "ConfigurationStore", lambda: store)

    assert cli.main(["--reset-config"]) == 0

    assert json.loads(store.settings_path.read_text(encoding="utf-8"))["schema_version"] == 1


def test_settings_control_compression_and_cli_options_override_them(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    input_path = tmp_path / "clip.mov"
    input_path.touch()
    output_directory = tmp_path / "outputs"
    output_directory.mkdir()
    store = ConfigurationStore(tmp_path / "config")
    raw = store.reset_settings().to_dict()
    raw["target_size_mb"] = 33
    raw["encoder"] = "cpu"
    raw["output"] = {"directory": str(output_directory), "suffix": "_discord"}
    raw["quality"] = {
        "auto_downscale": False,
        "target_bits_per_pixel": 0.1,
        "minimum_dimension": 160,
    }
    raw["audio"] = {
        "minimum_bitrate_kbps": 48,
        "maximum_bitrate_kbps": 64,
    }
    store.settings_path.write_text(json.dumps(raw), encoding="utf-8")

    captured = {}
    tools = SimpleNamespace(
        bundled=False,
        ffmpeg_path=Path("ffmpeg"),
        ffprobe_path=Path("ffprobe"),
    )

    def select(_tools, _store, **kwargs):
        captured["requested_encoder"] = kwargs["requested_encoder"]
        return EncoderSelection("amd", "h264_amf", "manual")

    def compress(*args, **kwargs):
        captured["compress_args"] = args
        captured["compress_kwargs"] = kwargs
        return object()

    monkeypatch.setattr(cli, "ConfigurationStore", lambda: store)
    monkeypatch.setattr(cli, "resolve_ffmpeg_tools", lambda: tools)
    monkeypatch.setattr(cli, "select_encoder", select)
    monkeypatch.setattr(cli, "compress_video", compress)
    monkeypatch.setattr(cli, "_print_result", lambda _result: None)

    assert cli.main([str(input_path), "--target-size", "44", "--encoder", "amd"]) == 0

    compress_args = captured["compress_args"]
    compress_kwargs = captured["compress_kwargs"]
    assert captured["requested_encoder"] == "amd"
    assert compress_args[1] == output_directory / "clip_discord.mp4"
    assert compress_args[2] == 44.0
    assert compress_kwargs["auto_downscale"] is False
    assert compress_kwargs["target_bits_per_pixel"] == 0.1
    assert compress_kwargs["minimum_dimension"] == 160
    assert compress_kwargs["min_audio_kbps"] == 48.0
    assert compress_kwargs["max_audio_kbps"] == 64.0


def test_redetect_encoder_ignores_configured_encoder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    store = ConfigurationStore(tmp_path)
    raw = store.reset_settings().to_dict()
    raw["encoder"] = "cpu"
    store.settings_path.write_text(json.dumps(raw), encoding="utf-8")
    captured = {}

    def select(_tools, _store, **kwargs):
        captured.update(kwargs)
        return EncoderSelection("cpu", "libx264", "detection")

    monkeypatch.setattr(cli, "ConfigurationStore", lambda: store)
    monkeypatch.setattr(cli, "resolve_ffmpeg_tools", lambda: SimpleNamespace(bundled=False))
    monkeypatch.setattr(cli, "select_encoder", select)

    assert cli.main(["--redetect-encoder"]) == 0

    assert captured["requested_encoder"] is None
    assert captured["force_redetection"] is True
