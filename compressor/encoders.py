"""Encoder settings, real initialization tests, selection, and cache policy."""

import logging
import math
import subprocess

import ffmpeg

from compressor.config import (
    ENCODER_TEST_BITRATE_KBPS,
    ENCODER_TEST_TIMEOUT_SECONDS,
)
from compressor.errors import ConfigurationError, EncoderUnavailableError, ValidationError
from compressor.models import EncoderSelection

LOGGER = logging.getLogger(__name__)

ENCODERS = {
    "nvidia": "h264_nvenc",
    "amd": "h264_amf",
    "intel": "h264_qsv",
    "mac": "h264_videotoolbox",
    "cpu": "libx264",
}

ENCODER_PRIORITY = ("nvidia", "amd", "intel", "mac", "cpu")

# Detection runs these exact production profiles. An encoder is considered
# unavailable if its driver rejects any required option; do not weaken the test
# independently of the real compression settings.
ENCODER_OPTIONS = {
    "nvidia": {
        "vf": "format=yuv420p",
        "preset": "p7",
        "tune": "hq",
        "rc": "vbr",
        "multipass": "fullres",
        "rc-lookahead": 20,
        "spatial-aq": 1,
        "temporal-aq": 0,
        "aq-strength": 10,
        "bf": 3,
        "b_ref_mode": "middle",
        "profile:v": "high",
    },
    "amd": {
        "vf": "format=yuv420p",
        "usage": "transcoding",
        "quality": "quality",
        "rc": "vbr_peak",
        "preencode": 1,
        "preanalysis": 1,
        "pa_lookahead_buffer_depth": 40,
        "pa_taq_mode": 2,
        "high_motion_quality_boost_enable": 1,
        "max_b_frames": 3,
        "pa_adaptive_mini_gop": 1,
        "profile:v": "high",
    },
    "intel": {
        "vf": "format=nv12",
        "preset": "veryslow",
        "look_ahead": 1,
        "look_ahead_depth": 40,
        "mbbrc": 1,
        "rdo": 1,
        "profile:v": "high",
    },
    "mac": {
        "vf": "format=nv12",
        "realtime": 0,
        "profile:v": "high",
    },
    "cpu": {
        "vf": "format=yuv420p",
        "preset": "slow",
        "profile:v": "high",
    },
}


def get_encoder_options(encoder_type, video_kbps):
    """Return an isolated copy of the quality and bitrate settings for an encoder."""
    if encoder_type not in ENCODER_OPTIONS:
        raise ValidationError(f"Unknown encoder type: '{encoder_type}'.")
    if not math.isfinite(video_kbps):
        raise ValidationError("Video bitrate must be a finite number.")

    options = ENCODER_OPTIONS[encoder_type].copy()
    average_kbps = max(1, round(video_kbps))
    options["b:v"] = f"{average_kbps}k"

    if encoder_type in {"nvidia", "amd", "intel", "cpu"}:
        options["maxrate"] = f"{round(average_kbps * 1.5)}k"
        options["bufsize"] = f"{round(average_kbps * 2)}k"
    return options


def is_encoder_available(
    encoder_type,
    tools,
    *,
    timeout=ENCODER_TEST_TIMEOUT_SECONDS,
):
    """Run a tiny real encode using the complete production option profile."""
    if encoder_type not in ENCODERS:
        return False

    source = ffmpeg.input(
        "color=c=black:s=320x180:r=30:d=0.25",
        f="lavfi",
    )
    graph = ffmpeg.output(
        source["v:0"],
        "pipe:",
        f="null",
        vcodec=ENCODERS[encoder_type],
        an=None,
        **{"frames:v": 6},
        **get_encoder_options(encoder_type, ENCODER_TEST_BITRATE_KBPS),
    ).global_args("-hide_banner", "-loglevel", "error", "-nostdin")

    try:
        result = tools.run_graph(graph, timeout=timeout)
    except subprocess.TimeoutExpired:
        LOGGER.debug("Encoder test timed out for %s", encoder_type)
        return False
    except OSError as exc:
        LOGGER.debug("Encoder test could not start for %s: %s", encoder_type, exc)
        return False

    if result.returncode == 0:
        return True

    stderr = result.stderr.decode("utf-8", errors="replace").strip()
    LOGGER.debug(
        "Encoder %s (%s) is absent or incompatible with the required quality "
        "profile. FFmpeg said:\n%s",
        encoder_type,
        ENCODERS[encoder_type],
        stderr or "(no diagnostic output)",
    )
    return False


def get_cached_encoder(
    config_store,
    tools,
    *,
    availability_checker=is_encoder_available,
    status_callback=None,
):
    """Load and validate only the cached encoder; never perform full detection."""
    record = config_store.load_encoder_cache(ENCODERS)
    if record is None:
        return None

    if status_callback:
        status_callback(f"Validating cached encoder: {record.preferred_encoder} ({record.encoder})")
    if not availability_checker(record.preferred_encoder, tools):
        LOGGER.warning(
            "Cached encoder %s no longer initializes; a full redetection is required.",
            record.preferred_encoder,
        )
        return None

    return EncoderSelection(
        encoder_type=record.preferred_encoder,
        encoder=record.encoder,
        source="cache",
    )


def detect_available_encoders(
    tools,
    *,
    availability_checker=is_encoder_available,
    status_callback=None,
):
    """Test every supported encoder once and return those that really initialize."""
    available = []
    for encoder_type in ENCODER_PRIORITY:
        if status_callback:
            status_callback(f"Testing encoder: {encoder_type} ({ENCODERS[encoder_type]})")
        if availability_checker(encoder_type, tools):
            available.append(encoder_type)
    return tuple(available)


def detect_preferred_encoder(
    tools,
    *,
    availability_checker=is_encoder_available,
    status_callback=None,
):
    """Perform a full supported-encoder scan and select the highest priority result."""
    available = detect_available_encoders(
        tools,
        availability_checker=availability_checker,
        status_callback=status_callback,
    )
    if not available:
        raise EncoderUnavailableError(
            "No supported H.264 encoder could initialize. This FFmpeg installation "
            "is unusable for the compressor because even libx264 is unavailable."
        )

    preferred = available[0]
    return EncoderSelection(
        encoder_type=preferred,
        encoder=ENCODERS[preferred],
        source="detection",
    )


def select_encoder(
    tools,
    config_store,
    *,
    requested_encoder=None,
    force_redetection=False,
    availability_checker=is_encoder_available,
    status_callback=None,
):
    """Select a manual, cached, or newly detected encoder under explicit cache rules."""
    # A manual choice is a per-run override. Return before reading or writing the
    # automatic cache so experiments cannot replace a known-good preference.
    if requested_encoder is not None:
        if requested_encoder not in ENCODERS:
            raise ValidationError(f"Unknown encoder type: '{requested_encoder}'.")
        if status_callback:
            status_callback(
                f"Validating requested encoder: {requested_encoder} ({ENCODERS[requested_encoder]})"
            )
        if not availability_checker(requested_encoder, tools):
            raise EncoderUnavailableError(
                f"The requested encoder '{requested_encoder}' "
                f"({ENCODERS[requested_encoder]}) could not initialize with the "
                "required quality settings. Use --debug for FFmpeg diagnostics."
            )
        return EncoderSelection(
            encoder_type=requested_encoder,
            encoder=ENCODERS[requested_encoder],
            source="manual",
        )

    if not force_redetection:
        cached = get_cached_encoder(
            config_store,
            tools,
            availability_checker=availability_checker,
            status_callback=status_callback,
        )
        if cached is not None:
            return cached

    selection = detect_preferred_encoder(
        tools,
        availability_checker=availability_checker,
        status_callback=status_callback,
    )
    try:
        config_store.save_encoder_cache(selection.encoder_type, selection.encoder)
    except ConfigurationError as exc:
        LOGGER.warning("%s Future launches will need to detect again.", exc)
        if exc.details:
            LOGGER.debug("Encoder cache write details: %s", exc.details)
    return selection
