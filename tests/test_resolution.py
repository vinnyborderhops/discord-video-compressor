import pytest

from compressor.errors import ValidationError
from compressor.resolution import calculate_output_resolution


def test_resolution_is_reduced_to_match_available_bits_per_pixel():
    assert calculate_output_resolution(1920, 1080, 30.0, 250.0) == (444, 250)


def test_resolution_is_unchanged_when_bitrate_is_sufficient():
    assert calculate_output_resolution(1280, 720, 30.0, 2500.0) == (1280, 720)


def test_resolution_uses_default_frame_rate_when_probe_has_none():
    assert calculate_output_resolution(1920, 1080, None, 250.0) == (444, 250)


def test_resolution_never_upscales_or_shrinks_below_encoder_safe_minimum():
    assert calculate_output_resolution(320, 180, 30.0, 1.0) == (228, 128)
    assert calculate_output_resolution(100, 100, 30.0, 1.0) == (100, 100)


@pytest.mark.parametrize("video_kbps", [0.0, -1.0, float("nan")])
def test_resolution_rejects_invalid_bitrate(video_kbps):
    with pytest.raises(ValidationError):
        calculate_output_resolution(1920, 1080, 30.0, video_kbps)
