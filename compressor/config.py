"""Application constants and persistent per-user configuration."""

import json
import logging
import math
import os
import sys
import tempfile
from collections import namedtuple
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from compressor.errors import ConfigurationError

LOGGER = logging.getLogger(__name__)

APPLICATION_NAME = "DiscordVideoCompressor"
SETTINGS_FILENAME = "settings.json"
ENCODER_CACHE_FILENAME = "encoder-cache.json"
LEGACY_ENCODER_CACHE_FILENAME = "config.json"
CONFIG_SCHEMA_VERSION = 1

DEFAULT_TARGET_SIZE_MB = 10.0
CONTAINER_EFFICIENCY = 0.97
MIN_AUDIO_KBPS = 64.0
MAX_AUDIO_KBPS = 96.0
LOW_VIDEO_BITRATE_WARNING_KBPS = 250.0

# Aim for enough H.264 bits per pixel per frame to retain useful visual detail.
# Resolution selection uses this as a quality floor and leaves smaller sources
# untouched when their bitrate already meets it.
TARGET_VIDEO_BITS_PER_PIXEL = 0.075
DEFAULT_VIDEO_FPS = 30.0
MINIMUM_OUTPUT_DIMENSION = 128

ENCODER_TEST_BITRATE_KBPS = 1_000.0
ENCODER_TEST_TIMEOUT_SECONDS = 15.0
EXECUTABLE_CHECK_TIMEOUT_SECONDS = 10.0
MINIMUM_DISK_HEADROOM_BYTES = 1024 * 1024
DISK_HEADROOM_FACTOR = 1.10
FFMPEG_STDERR_TAIL_LINES = 200

SETTING_ENCODERS = ("auto", "nvidia", "amd", "intel", "mac", "cpu")
INVALID_SUFFIX_CHARACTERS = frozenset('<>:"/\\|?*')


@dataclass(frozen=True)
class OutputSettings:
    directory: str = "source"
    suffix: str = "_compressed"


@dataclass(frozen=True)
class QualitySettings:
    auto_downscale: bool = True
    target_bits_per_pixel: float = TARGET_VIDEO_BITS_PER_PIXEL
    minimum_dimension: int = MINIMUM_OUTPUT_DIMENSION


@dataclass(frozen=True)
class AudioSettings:
    minimum_bitrate_kbps: float = MIN_AUDIO_KBPS
    maximum_bitrate_kbps: float = MAX_AUDIO_KBPS


@dataclass(frozen=True)
class ConsoleSettings:
    verbose: bool = False
    pause_on_exit: bool = True


@dataclass(frozen=True)
class ApplicationSettings:
    schema_version: int = CONFIG_SCHEMA_VERSION
    target_size_mb: float = DEFAULT_TARGET_SIZE_MB
    encoder: str = "auto"
    output: OutputSettings = OutputSettings()
    quality: QualitySettings = QualitySettings()
    audio: AudioSettings = AudioSettings()
    console: ConsoleSettings = ConsoleSettings()

    def to_dict(self):
        return asdict(self)


class EncoderCacheRecord(
    namedtuple(
        "EncoderCacheRecord",
        ("preferred_encoder", "encoder", "validated_at", "schema_version"),
        defaults=(CONFIG_SCHEMA_VERSION,),
    )
):
    """Validated representation of a persisted encoder cache entry."""

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
    """Load and atomically save user settings and application cache data."""

    def __init__(self, config_directory=None):
        self.config_directory = Path(config_directory or get_config_directory())
        self.settings_path = self.config_directory / SETTINGS_FILENAME
        self.encoder_cache_path = self.config_directory / ENCODER_CACHE_FILENAME
        self.legacy_encoder_cache_path = self.config_directory / LEGACY_ENCODER_CACHE_FILENAME
        # Compatibility for callers that previously used ``path`` for the cache.
        self.path = self.encoder_cache_path

    def load_settings(self):
        """Load validated settings, creating the defaults on first use."""
        try:
            raw = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return self.reset_settings()
        except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
            raise ConfigurationError(
                f"Could not read settings from '{self.settings_path}'.",
                details=str(exc),
            ) from exc

        try:
            return self._parse_settings(raw)
        except (TypeError, ValueError) as exc:
            raise ConfigurationError(
                f"Invalid settings in '{self.settings_path}': {exc}",
            ) from exc

    def reset_settings(self):
        """Replace the user settings with the current defaults."""
        settings = ApplicationSettings()
        self._atomic_write(
            self.settings_path,
            settings.to_dict(),
            error_message=f"Could not save settings in '{self.config_directory}'.",
        )
        return settings

    def load_encoder_cache(
        self,
        known_encoders,
    ):
        """Load a structurally valid cache record, treating corruption as a miss."""
        cache_path = self.encoder_cache_path
        if not cache_path.exists() and self.legacy_encoder_cache_path.exists():
            cache_path = self.legacy_encoder_cache_path

        try:
            raw = json.loads(cache_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
            LOGGER.warning("Ignoring unreadable encoder cache %s: %s", cache_path, exc)
            return None

        record = self._parse_record(raw, known_encoders)
        if record is None:
            LOGGER.warning("Ignoring invalid or stale encoder cache %s", cache_path)
            return None

        if cache_path == self.legacy_encoder_cache_path:
            try:
                self.save_encoder_cache(record.preferred_encoder, record.encoder)
            except ConfigurationError as exc:
                LOGGER.debug("Could not migrate legacy encoder cache: %s", exc)
        return record

    def save_encoder_cache(self, preferred_encoder, encoder):
        """Atomically write a validated encoder choice in UTF-8 JSON format."""
        record = EncoderCacheRecord(
            preferred_encoder=preferred_encoder,
            encoder=encoder,
            validated_at=datetime.now(timezone.utc).isoformat(),
        )
        self._atomic_write(
            self.encoder_cache_path,
            record.to_dict(),
            error_message=f"Could not save the encoder cache in '{self.config_directory}'.",
        )

    def _atomic_write(self, path, payload, *, error_message):
        try:
            self.config_directory.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{path.name}.",
                suffix=".tmp",
                dir=self.config_directory,
            )
            temporary_path = Path(temporary_name)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                    json.dump(payload, handle, indent=2)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary_path, path)
            except BaseException:
                temporary_path.unlink(missing_ok=True)
                raise
        except OSError as exc:
            raise ConfigurationError(error_message, details=str(exc)) from exc

    @staticmethod
    def _parse_settings(raw):
        root = _require_object(raw, "settings")
        schema_version = root.get("schema_version")
        if (
            isinstance(schema_version, bool)
            or not isinstance(schema_version, int)
            or schema_version != CONFIG_SCHEMA_VERSION
        ):
            raise ValueError(
                f"schema_version must be {CONFIG_SCHEMA_VERSION}; got {schema_version!r}"
            )

        target_size_mb = _positive_number(root.get("target_size_mb"), "target_size_mb")
        encoder = root.get("encoder")
        if encoder not in SETTING_ENCODERS:
            raise ValueError(f"encoder must be one of: {', '.join(SETTING_ENCODERS)}")

        output = _require_object(root.get("output"), "output")
        output_directory = _nonempty_string(output.get("directory"), "output.directory")
        if "\0" in output_directory:
            raise ValueError("output.directory cannot contain a null character")
        output_suffix = _string(output.get("suffix"), "output.suffix")
        if any(
            character in INVALID_SUFFIX_CHARACTERS or ord(character) < 32
            for character in output_suffix
        ):
            raise ValueError("output.suffix contains a character that is invalid in filenames")

        quality = _require_object(root.get("quality"), "quality")
        auto_downscale = _boolean(quality.get("auto_downscale"), "quality.auto_downscale")
        target_bits_per_pixel = _positive_number(
            quality.get("target_bits_per_pixel"),
            "quality.target_bits_per_pixel",
        )
        minimum_dimension = quality.get("minimum_dimension")
        if isinstance(minimum_dimension, bool) or not isinstance(minimum_dimension, int):
            raise ValueError("quality.minimum_dimension must be an integer")
        if minimum_dimension < 2:
            raise ValueError("quality.minimum_dimension must be at least 2")

        audio = _require_object(root.get("audio"), "audio")
        minimum_audio = _nonnegative_number(
            audio.get("minimum_bitrate_kbps"),
            "audio.minimum_bitrate_kbps",
        )
        maximum_audio = _nonnegative_number(
            audio.get("maximum_bitrate_kbps"),
            "audio.maximum_bitrate_kbps",
        )
        if minimum_audio > maximum_audio:
            raise ValueError("audio.minimum_bitrate_kbps cannot exceed audio.maximum_bitrate_kbps")

        console = _require_object(root.get("console"), "console")
        verbose = _boolean(console.get("verbose"), "console.verbose")
        pause_on_exit = _boolean(console.get("pause_on_exit"), "console.pause_on_exit")

        return ApplicationSettings(
            schema_version=schema_version,
            target_size_mb=target_size_mb,
            encoder=encoder,
            output=OutputSettings(directory=output_directory, suffix=output_suffix),
            quality=QualitySettings(
                auto_downscale=auto_downscale,
                target_bits_per_pixel=target_bits_per_pixel,
                minimum_dimension=minimum_dimension,
            ),
            audio=AudioSettings(
                minimum_bitrate_kbps=minimum_audio,
                maximum_bitrate_kbps=maximum_audio,
            ),
            console=ConsoleSettings(verbose=verbose, pause_on_exit=pause_on_exit),
        )

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


def _require_object(value, name):
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a JSON object")
    return value


def _string(value, name):
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    return value


def _nonempty_string(value, name):
    value = _string(value, name)
    if not value.strip():
        raise ValueError(f"{name} cannot be empty")
    return value


def _boolean(value, name):
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be true or false")
    return value


def _positive_number(value, name):
    value = _finite_number(value, name)
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


def _nonnegative_number(value, name):
    value = _finite_number(value, name)
    if value < 0:
        raise ValueError(f"{name} cannot be negative")
    return value


def _finite_number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value
