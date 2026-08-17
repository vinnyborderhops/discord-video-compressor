"""Target-size bitrate budget calculations."""

import math

from compressor.config import (
    CONTAINER_EFFICIENCY,
    LOW_VIDEO_BITRATE_WARNING_KBPS,
    MAX_AUDIO_KBPS,
    MIN_AUDIO_KBPS,
)
from compressor.errors import ValidationError
from compressor.models import BitrateBudget


def calculate_bitrate(
    duration_seconds,
    target_size_mb,
    has_audio,
    *,
    container_efficiency=CONTAINER_EFFICIENCY,
    min_audio_kbps=MIN_AUDIO_KBPS,
    max_audio_kbps=MAX_AUDIO_KBPS,
    low_video_warning_kbps=LOW_VIDEO_BITRATE_WARNING_KBPS,
):
    """Allocate a target-size budget between video and optional audio.

    Target megabytes use the prototype's MiB-style conversion (1 MB = 1024 KiB).
    The efficiency factor reserves room for MP4 container overhead.
    """
    _require_positive_finite(duration_seconds, "Video duration")
    _require_positive_finite(target_size_mb, "Target size")
    _require_positive_finite(container_efficiency, "Container efficiency")
    if container_efficiency > 1:
        raise ValidationError("Container efficiency must be no greater than 1.0.")
    _require_nonnegative_finite(min_audio_kbps, "Minimum audio bitrate")
    _require_nonnegative_finite(max_audio_kbps, "Maximum audio bitrate")
    _require_nonnegative_finite(low_video_warning_kbps, "Low video bitrate warning")
    if min_audio_kbps > max_audio_kbps:
        raise ValidationError("Minimum audio bitrate cannot exceed maximum audio bitrate.")

    total_kbps = target_size_mb * 8.0 * 1024.0 * container_efficiency / duration_seconds
    if not math.isfinite(total_kbps) or total_kbps <= 0:
        raise ValidationError(
            "The target size and duration produce a bitrate outside the supported range."
        )
    audio_kbps = 0.0
    if has_audio:
        audio_kbps = min(max_audio_kbps, max(min_audio_kbps, total_kbps * 0.15))

    video_kbps = total_kbps - audio_kbps
    if not math.isfinite(video_kbps) or video_kbps <= 0:
        raise ValidationError(
            "The target size is too small for the selected audio bitrate. "
            "Try a shorter video or a larger target size."
        )

    return BitrateBudget(
        total_kbps=total_kbps,
        video_kbps=video_kbps,
        audio_kbps=audio_kbps,
        low_video_bitrate=video_kbps < low_video_warning_kbps,
    )


def _require_positive_finite(value, name):
    if not math.isfinite(value) or value <= 0:
        raise ValidationError(f"{name} must be a positive, finite number.")


def _require_nonnegative_finite(value, name):
    if not math.isfinite(value) or value < 0:
        raise ValidationError(f"{name} must be a non-negative, finite number.")
