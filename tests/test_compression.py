import logging
from pathlib import Path

import pytest

from compressor.compression import (
    _build_compression_graph,
    _progress_percentage,
    _publish_output,
    compress_video,
)
from compressor.errors import CompressionError, OutputExistsError
from compressor.ffmpeg_tools import FFmpegExecutables, FFmpegTools
from compressor.models import BitrateBudget, VideoInfo


@pytest.fixture
def tools() -> FFmpegTools:
    return FFmpegTools(
        FFmpegExecutables(
            ffmpeg_path=Path("C:/Program Files/FFmpeg/ffmpeg.exe"),
            ffprobe_path=Path("C:/Program Files/FFmpeg/ffprobe.exe"),
            bundled=False,
        )
    )


@pytest.fixture
def budget() -> BitrateBudget:
    return BitrateBudget(1000.0, 900.0, 100.0, False)


def test_silent_graph_maps_only_video_and_pads_odd_dimensions(
    tools: FFmpegTools,
    budget: BitrateBudget,
):
    info = VideoInfo(
        path=Path("input silent.mov"),
        duration=5.0,
        width=641,
        height=359,
        video_codec="h264",
        has_audio=False,
        audio_codec=None,
        fps=30.0,
        video_stream_index=1,
    )
    command = tools.compile_graph(
        _build_compression_graph(
            info.path,
            Path("temporary output.mp4"),
            info,
            budget,
            "cpu",
        )
    )

    assert command[0] == str(tools.ffmpeg_path)
    assert "-an" in command
    assert "-acodec" not in command
    assert command.count("-map") == 1
    assert "0:v:1" in command
    assert "pad=ceil(iw/2)*2:ceil(ih/2)*2,format=yuv420p" in command


def test_audio_graph_maps_exactly_first_video_and_audio(
    tools: FFmpegTools,
    budget: BitrateBudget,
):
    info = VideoInfo(
        path=Path("input with audio.mkv"),
        duration=5.0,
        width=640,
        height=360,
        video_codec="vp9",
        has_audio=True,
        audio_codec="opus",
        fps=None,
        audio_stream_index=0,
    )
    command = tools.compile_graph(
        _build_compression_graph(
            info.path,
            Path("temporary output.mp4"),
            info,
            budget,
            "cpu",
        )
    )

    assert command.count("-map") == 2
    assert "0:v:0" in command
    assert "0:a:0" in command
    assert "-acodec" in command
    assert "aac" in command
    assert "-b:a" in command


@pytest.mark.parametrize(
    ("progress", "duration", "expected"),
    [
        ({"out_time_us": "2500000", "progress": "continue"}, 5.0, 50.0),
        ({"progress": "end"}, 5.0, 100.0),
        ({"out_time": "00:00:10.0", "progress": "continue"}, 5.0, 100.0),
    ],
)
def test_progress_parser_uses_microseconds_and_clamps(progress, duration, expected):
    assert _progress_percentage(progress, duration) == expected


def test_low_video_bitrate_is_logged_before_encoding(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    tools: FFmpegTools,
):
    input_path = tmp_path / "long input.mp4"
    input_path.touch()
    info = VideoInfo(
        path=input_path,
        duration=3600.0,
        width=640,
        height=360,
        video_codec="h264",
        has_audio=False,
        audio_codec=None,
        fps=30.0,
    )
    monkeypatch.setattr("compressor.compression.probe_video", lambda *_args, **_kwargs: info)

    def stop_after_warning(*_args, **_kwargs):
        raise CompressionError("stop after warning")

    monkeypatch.setattr("compressor.compression._validate_disk_space", stop_after_warning)

    with (
        caplog.at_level(logging.WARNING, logger="compressor.compression"),
        pytest.raises(CompressionError),
    ):
        compress_video(
            input_path,
            tmp_path / "output.mp4",
            10.0,
            "cpu",
            tools=tools,
        )

    assert "image quality will likely be poor" in caplog.text


def test_completed_temporary_output_is_published_without_overwrite(tmp_path: Path):
    temporary_path = tmp_path / ".output.part.mp4"
    output_path = tmp_path / "output.mp4"
    temporary_path.write_bytes(b"complete mp4 placeholder")

    _publish_output(temporary_path, output_path)

    assert output_path.read_bytes() == b"complete mp4 placeholder"
    assert not temporary_path.exists()

    second_temporary_path = tmp_path / ".second.part.mp4"
    second_temporary_path.write_bytes(b"must not replace")
    with pytest.raises(OutputExistsError):
        _publish_output(second_temporary_path, output_path)

    assert output_path.read_bytes() == b"complete mp4 placeholder"
