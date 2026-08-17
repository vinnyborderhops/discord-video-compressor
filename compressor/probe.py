"""FFprobe integration and normalization into :class:`VideoInfo`."""

import math
from collections.abc import Mapping, Sequence
from fractions import Fraction
from pathlib import Path

import ffmpeg

from compressor.errors import ProbeError, ValidationError
from compressor.ffmpeg_tools import resolve_ffmpeg_tools
from compressor.models import VideoInfo


def probe_video(path, tools=None):
    """Probe a local input and return validated, normalized metadata."""
    path = Path(path).expanduser()
    if not path.exists():
        raise ValidationError(f"Input video does not exist: '{path}'.")
    if not path.is_file():
        raise ValidationError(f"Input path is not a regular file: '{path}'.")

    tools = tools or resolve_ffmpeg_tools()
    try:
        raw = tools.probe(path)
    except ffmpeg.Error as exc:
        details = _decode_ffmpeg_bytes(exc.stderr)
        message = _probe_failure_message(details)
        raise ProbeError(message, details=details) from exc
    except PermissionError as exc:
        raise ProbeError(
            f"Permission was denied while reading '{path}'.",
            details=str(exc),
        ) from exc
    except OSError as exc:
        raise ProbeError(
            f"Could not read video metadata from '{path}'.",
            details=str(exc),
        ) from exc
    except (TypeError, ValueError) as exc:
        raise ProbeError(
            f"FFprobe returned malformed metadata for '{path}'.",
            details=str(exc),
        ) from exc

    return parse_probe_data(path, raw)


def parse_probe_data(path, raw):
    """Convert a raw FFprobe dictionary into a strict metadata model."""
    streams_value = raw.get("streams")
    if not isinstance(streams_value, Sequence) or isinstance(streams_value, (str, bytes)):
        raise ProbeError(f"FFprobe did not return stream metadata for '{path}'.")

    streams = [stream for stream in streams_value if isinstance(stream, Mapping)]
    video_candidates = [stream for stream in streams if stream.get("codec_type") == "video"]
    # ffmpeg-python's v:N/a:N selectors use per-media-type indexes, not absolute
    # positions in FFprobe's streams array. Keep these indexes type-relative.
    selected_video = next(
        (
            (index, stream)
            for index, stream in enumerate(video_candidates)
            if not _is_attached_picture(stream)
        ),
        None,
    )
    video_stream_index, video_stream = selected_video or (0, None)
    if video_stream is None:
        raise ProbeError(f"The input does not contain a supported video stream: '{path}'.")
    audio_stream = next(
        (stream for stream in streams if stream.get("codec_type") == "audio"),
        None,
    )

    width = _positive_integer(video_stream.get("width"), "video width", path)
    height = _positive_integer(video_stream.get("height"), "video height", path)
    video_codec = _required_text(video_stream.get("codec_name"), "video codec", path)

    format_value = raw.get("format")
    format_data = format_value if isinstance(format_value, Mapping) else {}
    # Some containers omit format duration and some streams omit stream duration;
    # Matroska-style DURATION tags are the final compatibility fallback.
    duration = _first_positive_float(
        format_data.get("duration"),
        video_stream.get("duration"),
        _duration_from_tags(video_stream.get("tags")),
    )
    if duration is None:
        raise ProbeError(
            f"The video duration is missing or invalid in '{path}'. "
            "The file may be corrupt or use an unsupported format."
        )

    fps = _parse_frame_rate(
        video_stream.get("avg_frame_rate"),
        video_stream.get("r_frame_rate"),
    )
    audio_codec = None
    if audio_stream is not None:
        candidate = audio_stream.get("codec_name")
        audio_codec = candidate if isinstance(candidate, str) and candidate else "unknown"

    return VideoInfo(
        path=path,
        duration=duration,
        width=width,
        height=height,
        video_codec=video_codec,
        has_audio=audio_stream is not None,
        audio_codec=audio_codec,
        fps=fps,
        video_stream_index=video_stream_index,
        audio_stream_index=0 if audio_stream is not None else None,
    )


def _positive_integer(value, field_name, path):
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ProbeError(f"FFprobe returned an invalid {field_name} for '{path}'.") from exc
    if parsed <= 0:
        raise ProbeError(f"FFprobe returned an invalid {field_name} for '{path}'.")
    return parsed


def _required_text(value, field_name, path):
    if not isinstance(value, str) or not value.strip():
        raise ProbeError(f"FFprobe did not identify the {field_name} for '{path}'.")
    return value


def _is_attached_picture(stream):
    disposition = stream.get("disposition")
    return isinstance(disposition, Mapping) and disposition.get("attached_pic") == 1


def _first_positive_float(*values):
    for value in values:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(parsed) and parsed > 0:
            return parsed
    return None


def _duration_from_tags(tags):
    if not isinstance(tags, Mapping):
        return None
    duration = tags.get("DURATION") or tags.get("duration")
    if not isinstance(duration, str):
        return None
    parts = duration.split(":")
    if len(parts) != 3:
        return None
    try:
        hours, minutes, seconds = (float(part) for part in parts)
    except ValueError:
        return None
    value = hours * 3600 + minutes * 60 + seconds
    return value if math.isfinite(value) and value > 0 else None


def _parse_frame_rate(*values):
    for value in values:
        if not isinstance(value, str) or not value or value == "0/0":
            continue
        try:
            parsed = float(Fraction(value))
        except (ValueError, ZeroDivisionError):
            continue
        if math.isfinite(parsed) and parsed > 0:
            return parsed
    return None


def _decode_ffmpeg_bytes(value):
    return value.decode("utf-8", errors="replace").strip() if value else ""


def _probe_failure_message(details):
    lowered = details.lower()
    if "permission denied" in lowered:
        return "FFprobe could not read the input because permission was denied."
    if "invalid data found" in lowered or "moov atom not found" in lowered:
        return "The input is corrupt or is not a supported video file."
    return "FFprobe could not read the input video. The file may be corrupt or unsupported."
