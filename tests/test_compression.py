import logging
from pathlib import Path

import pytest

from compressor.compression import (
    _build_compression_graph,
    _calculate_retry_bitrate,
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


def test_low_video_bitrate_selects_a_smaller_resolution_before_encoding(
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

    def stop_after_resolution_selection(*_args, **_kwargs):
        raise CompressionError("stop after resolution selection")

    monkeypatch.setattr(
        "compressor.compression._validate_disk_space", stop_after_resolution_selection
    )

    with (
        caplog.at_level(logging.INFO, logger="compressor.compression"),
        pytest.raises(CompressionError),
    ):
        compress_video(
            input_path,
            tmp_path / "output.mp4",
            10.0,
            "cpu",
            tools=tools,
        )

    assert "Downscaling video from 640x360 to 228x128" in caplog.text


def test_graph_downscales_to_selected_resolution(
    tools: FFmpegTools,
):
    info = VideoInfo(
        path=Path("large input.mp4"),
        duration=60.0,
        width=1920,
        height=1080,
        video_codec="h264",
        has_audio=False,
        audio_codec=None,
        fps=30.0,
    )
    low_budget = BitrateBudget(250.0, 250.0, 0.0, False)

    command = tools.compile_graph(
        _build_compression_graph(
            info.path,
            Path("small output.mp4"),
            info,
            low_budget,
            "cpu",
        )
    )

    assert "scale=444:250:flags=lanczos,format=yuv420p" in command


def test_retry_bitrate_corrects_measured_total_and_preserves_audio():
    current = BitrateBudget(1550.0, 1450.0, 100.0, False)

    corrected = _calculate_retry_bitrate(
        current,
        target_bytes=20 * 1024 * 1024,
        actual_bytes=20.75 * 1024 * 1024,
        safety_factor=0.995,
    )

    expected_total = 1550.0 * (20.0 / 20.75) * 0.995
    assert corrected.total_kbps == pytest.approx(expected_total)
    assert corrected.video_kbps == pytest.approx(expected_total - 100.0)
    assert corrected.audio_kbps == 100.0


def test_oversized_encodes_retry_until_target_is_reached(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tools: FFmpegTools,
):
    input_path = tmp_path / "input.mp4"
    input_path.write_bytes(b"source")
    output_path = tmp_path / "output.mp4"
    temporary_path = tmp_path / ".output.part.mp4"
    info = VideoInfo(
        path=input_path,
        duration=5.0,
        width=640,
        height=360,
        video_codec="h264",
        has_audio=True,
        audio_codec="aac",
        fps=30.0,
    )
    encoded_sizes = [
        int(1.0375 * 1024 * 1024),
        int(1.005 * 1024 * 1024),
        int(0.98 * 1024 * 1024),
    ]
    budgets = []
    statuses = []

    monkeypatch.setattr("compressor.compression.probe_video", lambda *_args: info)
    monkeypatch.setattr("compressor.compression._validate_disk_space", lambda *_args: None)
    monkeypatch.setattr(
        "compressor.compression._temporary_output_path", lambda *_args: temporary_path
    )
    monkeypatch.setattr("compressor.compression._verify_temporary_output", lambda *_args: None)

    def build_graph(_input, _output, _info, bitrate_budget, _encoder, **_kwargs):
        budgets.append(bitrate_budget)
        return object()

    def run_encode(*_args, **_kwargs):
        temporary_path.write_bytes(b"0" * encoded_sizes[len(budgets) - 1])
        return 0, ""

    monkeypatch.setattr("compressor.compression._build_compression_graph", build_graph)
    monkeypatch.setattr("compressor.compression._run_ffmpeg_with_progress", run_encode)

    result = compress_video(
        input_path,
        output_path,
        1.0,
        "cpu",
        tools=tools,
        status_callback=statuses.append,
    )

    assert result.met_target
    assert result.encoding_attempts == 3
    assert result.final_size_bytes == encoded_sizes[-1]
    assert len(budgets) == 3
    assert budgets[1].video_kbps < budgets[0].video_kbps
    assert budgets[2].video_kbps < budgets[1].video_kbps
    assert statuses[0] == "Encoding attempt 1/3..."
    assert "Target exceeded by 3.7%" in statuses[2]
    assert statuses[-1] == "Target reached."


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
