"""High-level target-size compression with progress and safe output publishing."""

import logging
import math
import os
import queue
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from pathlib import Path

import ffmpeg

from compressor.bitrate import calculate_bitrate
from compressor.config import (
    DISK_HEADROOM_FACTOR,
    FFMPEG_STDERR_TAIL_LINES,
    MINIMUM_DISK_HEADROOM_BYTES,
)
from compressor.encoders import ENCODERS, get_encoder_options
from compressor.errors import (
    CompressionError,
    CompressorError,
    InterruptedCompressionError,
    OutputExistsError,
    ProbeError,
    ValidationError,
)
from compressor.ffmpeg_tools import resolve_ffmpeg_tools
from compressor.models import CompressionResult
from compressor.probe import probe_video
from compressor.utils import path_is_occupied, paths_refer_to_same_location

LOGGER = logging.getLogger(__name__)


def compress_video(
    input_path,
    output_path,
    target_size_mb,
    encoder_type,
    *,
    tools=None,
    progress_callback=None,
):
    """Compress one video to an MP4 near or below the requested target size."""
    input_path, output_path = _validate_paths(input_path, output_path)
    _validate_target_size(target_size_mb)
    if encoder_type not in ENCODERS:
        raise ValidationError(f"Unknown encoder type: '{encoder_type}'.")

    tools = tools or resolve_ffmpeg_tools()
    video_info = probe_video(input_path, tools)
    bitrate_budget = calculate_bitrate(
        video_info.duration,
        target_size_mb,
        video_info.has_audio,
    )
    if bitrate_budget.low_video_bitrate:
        LOGGER.warning(
            "The target permits only %.1f kbps for video; image quality will likely be poor.",
            bitrate_budget.video_kbps,
        )
    _validate_disk_space(output_path.parent, target_size_mb)

    try:
        original_size = input_path.stat().st_size
    except OSError as exc:
        raise ValidationError(
            f"Could not read the input file size for '{input_path}'.",
            details=str(exc),
        ) from exc

    temporary_path = _temporary_output_path(output_path)
    graph = _build_compression_graph(
        input_path,
        temporary_path,
        video_info,
        bitrate_budget,
        encoder_type,
    )

    encode_started = time.monotonic()
    try:
        return_code, stderr_tail = _run_ffmpeg_with_progress(
            graph,
            tools,
            duration_seconds=video_info.duration,
            progress_callback=progress_callback,
        )
        encoding_duration = time.monotonic() - encode_started
        if return_code != 0:
            raise _compression_failure(stderr_tail, encoder_type)

        _verify_temporary_output(temporary_path, tools)
        _publish_output(temporary_path, output_path)
    except KeyboardInterrupt as exc:
        raise InterruptedCompressionError() from exc
    finally:
        _remove_temporary_output(temporary_path)

    try:
        final_size = output_path.stat().st_size
    except OSError as exc:
        raise CompressionError(
            f"The output was encoded but its final size could not be read: '{output_path}'.",
            details=str(exc),
        ) from exc

    compression_percentage = (
        (1.0 - final_size / original_size) * 100.0 if original_size > 0 else 0.0
    )
    target_bytes = target_size_mb * 1024.0 * 1024.0
    return CompressionResult(
        input_path=input_path,
        output_path=output_path,
        video_info=video_info,
        encoder_type=encoder_type,
        encoder=ENCODERS[encoder_type],
        original_size_bytes=original_size,
        final_size_bytes=final_size,
        compression_percentage=compression_percentage,
        encoding_duration_seconds=encoding_duration,
        target_size_mb=target_size_mb,
        met_target=final_size <= target_bytes,
        bitrate_budget=bitrate_budget,
    )


def _validate_paths(input_path, output_path):
    # Keep the final path itself intact so a broken symlink is still recognized
    # as an occupied output rather than silently resolved to its missing target.
    input_path = Path(os.path.abspath(Path(input_path).expanduser()))
    output_path = Path(os.path.abspath(Path(output_path).expanduser()))

    if not input_path.exists():
        raise ValidationError(f"Input video does not exist: '{input_path}'.")
    if not input_path.is_file():
        raise ValidationError(f"Input path is not a regular file: '{input_path}'.")
    if paths_refer_to_same_location(input_path, output_path):
        raise ValidationError("The output path cannot be the same as the source video.")
    if output_path.suffix.lower() != ".mp4":
        raise ValidationError("The output filename must use the .mp4 extension.")
    if path_is_occupied(output_path):
        raise OutputExistsError(f"Output already exists: '{output_path}'. Choose another filename.")
    if not output_path.parent.exists():
        raise ValidationError(f"Output directory does not exist: '{output_path.parent}'.")
    if not output_path.parent.is_dir():
        raise ValidationError(f"Output parent is not a directory: '{output_path.parent}'.")
    return input_path, output_path


def _validate_target_size(target_size_mb):
    if not math.isfinite(target_size_mb) or target_size_mb <= 0:
        raise ValidationError("Target size must be a positive, finite number of megabytes.")


def _validate_disk_space(directory, target_size_mb):
    required = max(
        int(target_size_mb * 1024 * 1024 * DISK_HEADROOM_FACTOR),
        int(target_size_mb * 1024 * 1024) + MINIMUM_DISK_HEADROOM_BYTES,
    )
    try:
        free = shutil.disk_usage(directory).free
    except OSError as exc:
        # This is an advisory preflight only. FFmpeg still reports an actionable
        # write failure if the platform cannot provide disk-usage information.
        LOGGER.debug("Could not determine free disk space for %s: %s", directory, exc)
        return
    if free < required:
        raise CompressionError(
            f"There is not enough free disk space in '{directory}'. "
            f"At least {required / (1024 * 1024):.1f} MiB is recommended."
        )


def _temporary_output_path(output_path):
    # Keep the temporary file beside the destination so hard-link/replace
    # publication stays on one filesystem and cannot fail with a cross-device move.
    for _ in range(10):
        candidate = output_path.with_name(f".{output_path.stem}.part-{uuid.uuid4().hex}.mp4")
        if not path_is_occupied(candidate):
            return candidate
    raise CompressionError("Could not allocate a unique temporary output filename.")


def _build_compression_graph(
    input_path,
    temporary_path,
    video_info,
    bitrate_budget,
    encoder_type,
):
    source = ffmpeg.input(str(input_path))
    encoder_options = get_encoder_options(encoder_type, bitrate_budget.video_kbps)

    # H.264 4:2:0 formats require even dimensions. Preserve the configured format
    # filter, adding only a one-pixel edge pad when an otherwise-valid input is odd.
    if video_info.width % 2 or video_info.height % 2:
        encoder_options["vf"] = "pad=ceil(iw/2)*2:ceil(ih/2)*2," + str(encoder_options["vf"])

    output_options = {
        "f": "mp4",
        "vcodec": ENCODERS[encoder_type],
        "movflags": "+faststart",
        **encoder_options,
    }
    mapped_streams = [source[f"v:{video_info.video_stream_index}"]]
    if video_info.has_audio:
        audio_stream_index = video_info.audio_stream_index
        if audio_stream_index is None:
            raise CompressionError("Audio metadata did not include a usable stream index.")
        mapped_streams.append(source[f"a:{audio_stream_index}"])
        output_options["acodec"] = "aac"
        output_options["b:a"] = f"{max(1, round(bitrate_budget.audio_kbps))}k"
    else:
        output_options["an"] = None

    graph = ffmpeg.output(
        *mapped_streams,
        str(temporary_path),
        **output_options,
    )
    return graph.global_args(
        "-hide_banner",
        "-nostdin",
        "-nostats",
        "-progress",
        "pipe:1",
    )


def _run_ffmpeg_with_progress(
    graph,
    tools,
    *,
    duration_seconds,
    progress_callback,
):
    # Give FFmpeg its own process group so an interrupt reaches the encoder and
    # any children it creates without signalling the compressor itself.
    process_kwargs = {}
    if sys.platform == "win32":
        process_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        process_kwargs["start_new_session"] = True

    try:
        process = tools.popen_graph(
            graph,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            **process_kwargs,
        )
    except PermissionError as exc:
        raise CompressionError(
            "Permission was denied while starting FFmpeg or creating the output.",
            details=str(exc),
        ) from exc
    except OSError as exc:
        raise CompressionError("FFmpeg could not be started.", details=str(exc)) from exc

    reader_threads = []
    try:
        progress_values = queue.Queue()
        stderr_tail = deque(maxlen=FFMPEG_STDERR_TAIL_LINES)
        # Drain both pipes concurrently. FFmpeg may fill stderr while stdout is
        # idle (or vice versa), and waiting on either pipe alone can deadlock it.
        reader_threads = [
            threading.Thread(
                target=_read_progress_stream,
                args=(process.stdout, duration_seconds, progress_values),
                name="ffmpeg-progress-reader",
                daemon=True,
            ),
            threading.Thread(
                target=_read_stderr_stream,
                args=(process.stderr, stderr_tail),
                name="ffmpeg-stderr-reader",
                daemon=True,
            ),
        ]
        for reader_thread in reader_threads:
            reader_thread.start()

        # Invoke callbacks on this coordinating thread rather than the pipe
        # readers so callers do not need to make UI or terminal updates thread-safe.
        while process.poll() is None:
            _deliver_progress(progress_values, progress_callback, timeout=0.2)
        _join_started_threads(reader_threads)
        _deliver_all_progress(progress_values, progress_callback)
        return process.returncode, "\n".join(stderr_tail)
    except KeyboardInterrupt:
        _cancel_process(process)
        _join_started_threads(reader_threads)
        raise InterruptedCompressionError() from None
    except BaseException:
        _cancel_process(process)
        _join_started_threads(reader_threads)
        raise


def _join_started_threads(reader_threads):
    for reader_thread in reader_threads:
        if reader_thread.ident is not None:
            reader_thread.join()


def _read_progress_stream(
    stream,
    duration_seconds,
    progress_values,
):
    if stream is None:
        return
    record = {}
    try:
        for raw_line in stream:
            line = raw_line.strip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            record[key] = value
            if key == "progress":
                percentage = _progress_percentage(record, duration_seconds)
                if percentage is not None:
                    progress_values.put(percentage)
                record = {}
    except (OSError, ValueError) as exc:
        LOGGER.debug("FFmpeg progress reader stopped: %s", exc)


def _read_stderr_stream(stream, tail):
    if stream is None:
        return
    try:
        for raw_line in stream:
            line = raw_line.rstrip()
            if line:
                tail.append(line)
                LOGGER.debug("ffmpeg: %s", line)
    except OSError as exc:
        LOGGER.debug("FFmpeg stderr reader stopped: %s", exc)


def _progress_percentage(record, duration_seconds):
    if record.get("progress") == "end":
        return 100.0

    elapsed_seconds = None
    # Despite its name, FFmpeg's legacy out_time_ms progress value is expressed
    # in microseconds. Newer versions may report the clearer out_time_us key.
    for key in ("out_time_us", "out_time_ms"):
        value = record.get(key)
        if value is None:
            continue
        try:
            elapsed_seconds = float(value) / 1_000_000.0
        except ValueError:
            continue
        break

    if elapsed_seconds is None and "out_time" in record:
        elapsed_seconds = _parse_timestamp(record["out_time"])
    if elapsed_seconds is None or duration_seconds <= 0:
        return None
    return min(100.0, max(0.0, elapsed_seconds / duration_seconds * 100.0))


def _parse_timestamp(value):
    parts = value.split(":")
    if len(parts) != 3:
        return None
    try:
        hours, minutes, seconds = (float(part) for part in parts)
    except ValueError:
        return None
    parsed = hours * 3600.0 + minutes * 60.0 + seconds
    return parsed if math.isfinite(parsed) and parsed >= 0 else None


def _deliver_progress(
    values,
    callback,
    *,
    timeout,
):
    try:
        value = values.get(timeout=timeout)
    except queue.Empty:
        return
    if callback:
        callback(value)


def _deliver_all_progress(
    values,
    callback,
):
    while True:
        try:
            value = values.get_nowait()
        except queue.Empty:
            return
        if callback:
            callback(value)


def _cancel_process(process):
    if process.poll() is not None:
        return
    # Prefer a graceful interrupt so FFmpeg can finalize and release resources;
    # escalate only when it does not exit within the bounded waits.
    try:
        if sys.platform == "win32":
            process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            os.killpg(process.pid, signal.SIGINT)
        process.wait(timeout=3.0)
        return
    except (OSError, subprocess.SubprocessError):
        pass

    try:
        process.terminate()
        process.wait(timeout=2.0)
        return
    except (OSError, subprocess.SubprocessError):
        pass

    try:
        process.kill()
        process.wait(timeout=2.0)
    except (OSError, subprocess.SubprocessError):
        LOGGER.debug("FFmpeg did not exit cleanly after cancellation", exc_info=True)


def _verify_temporary_output(path, tools):
    try:
        size = path.stat().st_size
    except FileNotFoundError as exc:
        raise CompressionError(
            "FFmpeg reported success but did not create an output file."
        ) from exc
    except OSError as exc:
        raise CompressionError(
            "The encoded temporary output could not be inspected.",
            details=str(exc),
        ) from exc
    if size <= 0:
        raise CompressionError("FFmpeg created an empty output file.")

    try:
        probe_video(path, tools)
    except (ProbeError, ValidationError) as exc:
        raise CompressionError(
            "FFmpeg created an output file, but FFprobe could not validate it.",
            details=exc.details if isinstance(exc, CompressorError) else str(exc),
        ) from exc


def _publish_output(temporary_path, output_path):
    """Publish a complete file without overwriting a concurrently-created output."""
    # A hard link is an atomic no-overwrite publication on filesystems that
    # support it: the destination immediately names the already-complete inode.
    try:
        os.link(temporary_path, output_path)
    except FileExistsError as exc:
        raise OutputExistsError(
            f"Output appeared while encoding and was not overwritten: '{output_path}'."
        ) from exc
    except OSError:
        _publish_with_reserved_destination(temporary_path, output_path)
    else:
        try:
            temporary_path.unlink()
        except OSError:
            LOGGER.debug("Could not remove linked temporary output %s", temporary_path)


def _publish_with_reserved_destination(temporary_path, output_path):
    descriptor = None
    placeholder_created = False
    try:
        # O_EXCL reserves the name without a check-then-create race. Keeping that
        # name occupied lets os.replace overwrite our placeholder on filesystems
        # where hard-link publication is unavailable.
        descriptor = os.open(
            output_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o666,
        )
        placeholder_created = True
        os.close(descriptor)
        descriptor = None
        os.replace(temporary_path, output_path)
    except FileExistsError as exc:
        raise OutputExistsError(
            f"Output appeared while encoding and was not overwritten: '{output_path}'."
        ) from exc
    except PermissionError as exc:
        raise CompressionError(
            f"Permission was denied while publishing '{output_path}'.",
            details=str(exc),
        ) from exc
    except OSError as exc:
        raise CompressionError(
            f"The completed video could not be moved to '{output_path}'.",
            details=str(exc),
        ) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        # A successful replace consumes the temporary path. If it remains after
        # failure, remove the zero-byte reservation left by this publication attempt.
        if placeholder_created and temporary_path.exists() and output_path.exists():
            try:
                if output_path.stat().st_size == 0:
                    output_path.unlink()
            except OSError:
                LOGGER.debug("Could not remove output placeholder %s", output_path)


def _compression_failure(stderr_tail, encoder_type):
    lowered = stderr_tail.lower()
    if "no space left on device" in lowered or "not enough space" in lowered:
        message = "FFmpeg ran out of disk space while writing the output."
    elif "permission denied" in lowered:
        message = "FFmpeg was denied permission to read the input or write the output."
    elif "unknown encoder 'aac'" in lowered or 'unknown encoder "aac"' in lowered:
        message = "This FFmpeg installation does not provide the required AAC audio encoder."
    elif "unknown encoder" in lowered:
        message = f"FFmpeg no longer provides the selected encoder '{encoder_type}'."
    elif "error while opening encoder" in lowered or "cannot load" in lowered:
        message = (
            f"The selected encoder '{encoder_type}' failed to initialize during compression. "
            "Try --redetect-encoder."
        )
    else:
        message = "FFmpeg could not encode the video. Use --debug for diagnostic details."
    return CompressionError(message, details=stderr_tail or None)


def _remove_temporary_output(path):
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        LOGGER.warning("Could not remove temporary output '%s': %s", path, exc)
