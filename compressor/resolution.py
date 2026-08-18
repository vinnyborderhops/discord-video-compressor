"""Bitrate-aware output resolution selection."""

import math

from compressor.config import (
    DEFAULT_VIDEO_FPS,
    MINIMUM_OUTPUT_DIMENSION,
    TARGET_VIDEO_BITS_PER_PIXEL,
)
from compressor.errors import ValidationError


def calculate_output_resolution(
    width,
    height,
    fps,
    video_kbps,
    *,
    target_bits_per_pixel=TARGET_VIDEO_BITS_PER_PIXEL,
    default_fps=DEFAULT_VIDEO_FPS,
    minimum_dimension=MINIMUM_OUTPUT_DIMENSION,
):
    """Return an aspect-ratio-preserving resolution for a bitrate budget.

    The source resolution is retained when its estimated bits per pixel per frame
    meets the quality target. Otherwise, its pixel count is reduced to match the
    available video bitrate. Downscaled dimensions are even, and very small
    outputs are bounded for broad hardware encoder compatibility.
    """
    _require_positive_finite(width, "Video width")
    _require_positive_finite(height, "Video height")
    _require_positive_finite(video_kbps, "Video bitrate")
    _require_positive_finite(target_bits_per_pixel, "Target bits per pixel")
    _require_positive_finite(default_fps, "Default frame rate")
    if not isinstance(minimum_dimension, int) or minimum_dimension < 2:
        raise ValidationError("Minimum output dimension must be an integer of at least 2.")

    effective_fps = fps if fps is not None and math.isfinite(fps) and fps > 0 else default_fps
    source_pixels = width * height
    affordable_pixels = video_kbps * 1000.0 / (effective_fps * target_bits_per_pixel)
    if affordable_pixels >= source_pixels:
        return int(width), int(height)

    scale = math.sqrt(affordable_pixels / source_pixels)
    scale = max(scale, minimum_dimension / min(width, height))
    scale = min(1.0, scale)

    output_width = _nearest_even(width * scale)
    output_height = _nearest_even(height * scale)
    return min(int(width), output_width), min(int(height), output_height)


def _nearest_even(value):
    return max(2, int(value / 2.0 + 0.5) * 2)


def _require_positive_finite(value, name):
    try:
        valid = math.isfinite(value) and value > 0
    except TypeError:
        valid = False
    if not valid:
        raise ValidationError(f"{name} must be a positive, finite number.")
