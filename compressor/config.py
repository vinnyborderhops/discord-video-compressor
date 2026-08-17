"""Application constants and persistent per-user encoder configuration."""

import json
import logging
import math
import os
import sys
import tempfile
from collections import namedtuple
from datetime import datetime, timezone
from pathlib import Path

from compressor.errors import ConfigurationError

LOGGER = logging.getLogger(__name__)

APPLICATION_NAME = "DiscordVideoCompressor"
CONFIG_FILENAME = "config.json"
CONFIG_SCHEMA_VERSION = 1

DEFAULT_TARGET_SIZE_MB = 20.0
CONTAINER_EFFICIENCY = 0.97
MIN_AUDIO_KBPS = 64.0
MAX_AUDIO_KBPS = 96.0
LOW_VIDEO_BITRATE_WARNING_KBPS = 250.0

ENCODER_TEST_BITRATE_KBPS = 1_000.0
ENCODER_TEST_TIMEOUT_SECONDS = 15.0
EXECUTABLE_CHECK_TIMEOUT_SECONDS = 10.0
MINIMUM_DISK_HEADROOM_BYTES = 1024 * 1024
DISK_HEADROOM_FACTOR = 1.10
FFMPEG_STDERR_TAIL_LINES = 200


class EncoderCacheRecord(
    namedtuple(
        "EncoderCacheRecord",
        ("preferred_encoder", "encoder", "validated_at", "schema_version"),
        defaults=(CONFIG_SCHEMA_VERSION,),
    )
):
    """Validated representation of the encoder entry stored in config.json."""

    __slots__ = ()

    def to_dict(self):
        return {
            "schema_version": self.schema_version,
            "preferred_encoder": self.preferred_encoder,
            "encoder": self.encoder,
            "validated_at": self.validated_at,
        }


def get_config_directory(
    *,
    platform_name=None,
    environ=None,
    home=None,
):
    """Return the conventional per-user application configuration directory."""
    platform_name = platform_name or sys.platform
    environ = os.environ if environ is None else environ
    home = Path.home() if home is None else home

    if platform_name == "win32":
        local_app_data = environ.get("LOCALAPPDATA")
        base = Path(local_app_data) if local_app_data else home / "AppData" / "Local"
        return base / APPLICATION_NAME

    if platform_name == "darwin":
        return home / "Library" / "Application Support" / APPLICATION_NAME

    xdg_config_home = environ.get("XDG_CONFIG_HOME")
    base = Path(xdg_config_home) if xdg_config_home else home / ".config"
    return base / "discord-video-compressor"


class ConfigurationStore:
    """Load and atomically save the application's encoder cache."""

    def __init__(self, config_directory=None):
        self.config_directory = config_directory or get_config_directory()
        self.path = self.config_directory / CONFIG_FILENAME

    def load_encoder_cache(
        self,
        known_encoders,
    ):
        """Load a structurally valid cache record, treating corruption as a miss."""
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
            LOGGER.warning("Ignoring unreadable encoder cache %s: %s", self.path, exc)
            return None

        record = self._parse_record(raw, known_encoders)
        if record is None:
            LOGGER.warning("Ignoring invalid or stale encoder cache %s", self.path)
        return record

    def save_encoder_cache(self, preferred_encoder, encoder):
        """Atomically write a validated encoder choice in UTF-8 JSON format."""
        record = EncoderCacheRecord(
            preferred_encoder=preferred_encoder,
            encoder=encoder,
            validated_at=datetime.now(timezone.utc).isoformat(),
        )
        try:
            self.config_directory.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{CONFIG_FILENAME}.",
                suffix=".tmp",
                dir=self.config_directory,
            )
            temporary_path = Path(temporary_name)
            try:
                # Create the temporary file in the config directory so replace is
                # same-filesystem and atomic; flush/fsync it before publishing the name.
                with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                    json.dump(record.to_dict(), handle, indent=2, sort_keys=True)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary_path, self.path)
            except BaseException:
                temporary_path.unlink(missing_ok=True)
                raise
        except OSError as exc:
            raise ConfigurationError(
                f"Could not save the encoder cache in '{self.config_directory}'.",
                details=str(exc),
            ) from exc

    @staticmethod
    def _parse_record(
        raw,
        known_encoders,
    ):
        if not isinstance(raw, dict):
            return None
        if raw.get("schema_version") != CONFIG_SCHEMA_VERSION:
            return None

        preferred_encoder = raw.get("preferred_encoder")
        encoder = raw.get("encoder")
        validated_at = raw.get("validated_at")
        if not all(isinstance(value, str) for value in (preferred_encoder, encoder, validated_at)):
            return None
        if known_encoders.get(preferred_encoder) != encoder:
            return None

        # The timestamp proves the persisted shape is parseable but is not an
        # expiry policy. Encoder availability is tested again before every cache use.
        try:
            parsed_time = datetime.fromisoformat(validated_at)
        except (ValueError, OverflowError, OSError):
            return None
        if parsed_time.tzinfo is None or not math.isfinite(parsed_time.timestamp()):
            return None

        return EncoderCacheRecord(
            preferred_encoder=preferred_encoder,
            encoder=encoder,
            validated_at=validated_at,
        )
