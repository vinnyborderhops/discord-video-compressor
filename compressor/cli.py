"""Command-line and drag-and-drop interface."""

import argparse
import logging
import math
import sys
from pathlib import Path

from compressor import __version__
from compressor.compression import compress_video
from compressor.config import DEFAULT_TARGET_SIZE_MB, ConfigurationStore
from compressor.encoders import ENCODERS, select_encoder
from compressor.errors import CompressorError, ValidationError
from compressor.ffmpeg_tools import resolve_ffmpeg_tools
from compressor.utils import format_bytes, generate_output_path

LOGGER = logging.getLogger(__name__)


def build_parser():
    """Create the CLI parser without touching FFmpeg or the filesystem."""
    parser = argparse.ArgumentParser(
        prog="compressor",
        description=(
            "Compress a video to a target MP4 file size using the best working "
            "hardware encoder, with libx264 as the fallback."
        ),
    )
    parser.add_argument(
        "input",
        nargs="?",
        type=Path,
        help="input video (also supplied when a file is dragged onto the executable)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="output MP4 path (default: INPUT_compressed.mp4 with collision avoidance)",
    )
    parser.add_argument(
        "-t",
        "--target-size",
        type=float,
        default=DEFAULT_TARGET_SIZE_MB,
        metavar="MB",
        help=f"target output size in MiB-style MB (default: {DEFAULT_TARGET_SIZE_MB:g})",
    )
    encoder_group = parser.add_mutually_exclusive_group()
    encoder_group.add_argument(
        "--encoder",
        choices=tuple(ENCODERS),
        help="temporarily use and validate one encoder without changing the cache",
    )
    encoder_group.add_argument(
        "--redetect-encoder",
        action="store_true",
        help="ignore the cache, test supported encoders, and update the cache",
    )
    parser.add_argument(
        "--show-encoder",
        action="store_true",
        help="validate and display the selected encoder, then exit",
    )
    logging_group = parser.add_mutually_exclusive_group()
    logging_group.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="show additional application status",
    )
    logging_group.add_argument(
        "--debug",
        action="store_true",
        help="show detailed application and FFmpeg diagnostics",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def main(argv=None):
    """Run the application using explicit arguments or the process command line."""
    if argv is None:
        argv = sys.argv[1:]

    parser = build_parser()
    args = parser.parse_args(argv)
    _configure_logging(verbose=args.verbose, debug=args.debug)

    if args.input is None and not (args.show_encoder or args.redetect_encoder):
        parser.error(
            "an input video is required unless --show-encoder or --redetect-encoder is used"
        )
    if args.output is not None and args.input is None:
        parser.error("--output requires an input video")

    try:
        if args.input is not None:
            _validate_cli_target(args.target_size)

        tools = resolve_ffmpeg_tools()
        config_store = ConfigurationStore()
        if args.verbose or args.debug:
            source = "bundled" if tools.bundled else "PATH"
            print(f"FFmpeg ({source}): {tools.ffmpeg_path}")
            print(f"FFprobe ({source}): {tools.ffprobe_path}")

        selection = select_encoder(
            tools,
            config_store,
            requested_encoder=args.encoder,
            force_redetection=args.redetect_encoder,
            status_callback=print,
        )

        if args.show_encoder or args.input is None:
            print(
                f"Selected encoder: {selection.encoder_type} ({selection.encoder}) "
                f"[{selection.source}]"
            )
            if selection.source != "manual":
                print(f"Encoder cache: {config_store.path}")
            return 0

        output_path = args.output or generate_output_path(args.input)
        print(f"Input: {args.input}")
        print(f"Output: {output_path}")
        print(f"Encoder: {selection.encoder_type} ({selection.encoder}) [{selection.source}]")
        print(f"Target: {args.target_size:g} MB")

        progress = ConsoleProgress()
        try:
            result = compress_video(
                args.input,
                output_path,
                args.target_size,
                selection.encoder_type,
                tools=tools,
                progress_callback=progress.update,
            )
        finally:
            progress.finish()
        _print_result(result)
        return 0
    except CompressorError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        if args.debug and exc.details:
            print(f"\nDiagnostic details:\n{exc.details}", file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        print("\nError: operation interrupted.", file=sys.stderr)
        return 130
    except Exception:
        if args.debug:
            LOGGER.exception("Unexpected application failure")
        else:
            print(
                "Error: an unexpected application error occurred. "
                "Run again with --debug for details.",
                file=sys.stderr,
            )
        return 1


class ConsoleProgress:
    """Compact terminal progress suitable for terminals and redirected logs."""

    def __init__(self):
        self._interactive = bool(getattr(sys.stdout, "isatty", lambda: False)())
        self._last_bucket = -1
        self._line_open = False

    def update(self, percentage):
        percentage = min(100.0, max(0.0, percentage))
        if self._interactive:
            print(f"\rProgress: {percentage:6.2f}%", end="", flush=True)
            self._line_open = True
            if percentage >= 100.0:
                print()
                self._line_open = False
            return

        bucket = min(10, int(percentage // 10))
        if bucket > self._last_bucket:
            self._last_bucket = bucket
            print(f"Progress: {percentage:.1f}%")

    def finish(self):
        if self._line_open:
            print()
            self._line_open = False


def _validate_cli_target(target_size_mb):
    if not math.isfinite(target_size_mb) or target_size_mb <= 0:
        raise ValidationError("Target size must be a positive, finite number of megabytes.")


def _configure_logging(*, verbose, debug):
    level = logging.DEBUG if debug else logging.INFO if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(levelname)s: %(message)s",
    )


def _print_result(result):
    info = result.video_info
    fps = f"{info.fps:.3f}" if info.fps is not None else "unknown"
    audio = info.audio_codec if info.has_audio else "none"
    target_status = "yes" if result.met_target else "NO (encoder overshoot)"
    print("Compression complete.")
    print(f"Media: {info.width}x{info.height}, {fps} fps, video={info.video_codec}, audio={audio}")
    print(f"Original size: {format_bytes(result.original_size_bytes)}")
    print(f"Final size: {format_bytes(result.final_size_bytes)}")
    print(f"Size reduction: {result.compression_percentage:.2f}%")
    print(f"Selected encoder: {result.encoder_type} ({result.encoder})")
    print(f"Encoding duration: {result.encoding_duration_seconds:.2f} seconds")
    print(f"Target size: {result.target_size_mb:g} MB")
    print(f"Met target: {target_status}")
    print(f"Saved to: {result.output_path}")
