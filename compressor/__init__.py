"""Production-quality, target-size-oriented video compression."""

from compressor.bitrate import calculate_bitrate
from compressor.compression import compress_video
from compressor.encoders import get_encoder_options

__all__ = ["calculate_bitrate", "compress_video", "get_encoder_options"]

__version__ = "1.0.0"
