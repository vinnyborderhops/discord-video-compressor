from pathlib import Path

import pytest

from compressor.errors import ProbeError
from compressor.probe import parse_probe_data


def test_normalizes_video_audio_and_fractional_fps():
    info = parse_probe_data(
        Path("example video.mp4"),
        {
            "format": {"duration": "12.5"},
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 1920,
                    "height": 1080,
                    "avg_frame_rate": "30000/1001",
                },
                {"codec_type": "audio", "codec_name": "aac"},
            ],
        },
    )
    assert info.video_codec == "h264"
    assert info.has_audio
    assert info.audio_codec == "aac"
    assert info.fps == pytest.approx(29.97002997)


def test_silent_input_and_stream_duration_fallback():
    info = parse_probe_data(
        Path("silent.mkv"),
        {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "vp9",
                    "width": "641",
                    "height": "359",
                    "duration": "4.25",
                    "avg_frame_rate": "0/0",
                }
            ]
        },
    )
    assert not info.has_audio
    assert info.audio_codec is None
    assert info.fps is None


def test_missing_video_stream_is_rejected():
    with pytest.raises(ProbeError):
        parse_probe_data(
            Path("audio-only.m4a"),
            {"format": {"duration": "2"}, "streams": [{"codec_type": "audio"}]},
        )


def test_attached_picture_is_not_selected_as_the_video():
    info = parse_probe_data(
        Path("video-with-cover.mp4"),
        {
            "format": {"duration": "10"},
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "mjpeg",
                    "width": 600,
                    "height": 600,
                    "disposition": {"attached_pic": 1},
                },
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 1920,
                    "height": 1080,
                    "avg_frame_rate": "24/1",
                },
            ],
        },
    )
    assert info.video_codec == "h264"
    assert info.video_stream_index == 1


def test_attached_picture_only_file_is_not_treated_as_video():
    with pytest.raises(ProbeError):
        parse_probe_data(
            Path("audio-with-cover.m4a"),
            {
                "format": {"duration": "10"},
                "streams": [
                    {
                        "codec_type": "video",
                        "codec_name": "mjpeg",
                        "width": 600,
                        "height": 600,
                        "disposition": {"attached_pic": 1},
                    },
                    {"codec_type": "audio", "codec_name": "aac"},
                ],
            },
        )
