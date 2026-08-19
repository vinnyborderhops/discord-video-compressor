"""Data models shared by the compressor modules."""

from collections import namedtuple


class VideoInfo(
    namedtuple(
        "VideoInfo",
        (
            "path",
            "duration",
            "width",
            "height",
            "video_codec",
            "has_audio",
            "audio_codec",
            "fps",
            "video_stream_index",
            "audio_stream_index",
        ),
        defaults=(0, None),
    )
):
    """Normalized metadata for the first video and optional audio stream."""

    __slots__ = ()


class BitrateBudget(
    namedtuple(
        "BitrateBudget",
        ("total_kbps", "video_kbps", "audio_kbps", "low_video_bitrate"),
    )
):
    """Calculated total, video, and audio bitrate allocation in kilobits/s."""

    __slots__ = ()

    def __iter__(self):
        """Yield only the legacy three values; the warning flag is intentionally excluded."""
        yield self.total_kbps
        yield self.video_kbps
        yield self.audio_kbps


class EncoderSelection(namedtuple("EncoderSelection", ("encoder_type", "encoder", "source"))):
    """A validated encoder choice and how it was selected."""

    __slots__ = ()


class CompressionResult(
    namedtuple(
        "CompressionResult",
        (
            "input_path",
            "output_path",
            "video_info",
            "encoder_type",
            "encoder",
            "original_size_bytes",
            "final_size_bytes",
            "compression_percentage",
            "encoding_duration_seconds",
            "target_size_mb",
            "met_target",
            "bitrate_budget",
            "output_resolution",
            "encoding_attempts",
        ),
        # Preserve positional construction from before retry counts were exposed.
        defaults=(None, 1),
    )
):
    """Summary of a completed compression operation."""

    __slots__ = ()
