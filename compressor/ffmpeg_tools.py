"""Centralized FFmpeg/FFprobe resolution and process launching."""

import contextlib
import ctypes
import logging
import os
import shutil
import subprocess
import sys
import threading
from collections import namedtuple
from pathlib import Path

import ffmpeg

from compressor.config import EXECUTABLE_CHECK_TIMEOUT_SECONDS
from compressor.errors import ExecutableNotFoundError

LOGGER = logging.getLogger(__name__)

BUNDLED_BINARY_DIRECTORY = "ffmpeg-bin"
# Both os.environ and Windows' DLL directory are process-wide. Serialize the
# temporary changes below so concurrent probes and launches cannot observe or
# restore one another's intermediate state.
_RUNTIME_ENVIRONMENT_LOCK = threading.RLock()


class FFmpegExecutables(
    namedtuple("FFmpegExecutables", ("ffmpeg_path", "ffprobe_path", "bundled"))
):
    """Resolved absolute executable paths that must always be used as a pair."""

    __slots__ = ()


class FFmpegTools:
    """Compile ffmpeg-python graphs and launch only the resolved executables."""

    def __init__(self, executables):
        self.executables = executables

    @property
    def ffmpeg_path(self):
        return self.executables.ffmpeg_path

    @property
    def ffprobe_path(self):
        return self.executables.ffprobe_path

    @property
    def bundled(self):
        return self.executables.bundled

    def compile_graph(
        self,
        stream_spec,
        *,
        overwrite_output=False,
    ):
        """Compile a graph with the resolved FFmpeg path as argv[0]."""
        return ffmpeg.compile(
            stream_spec,
            cmd=str(self.ffmpeg_path),
            overwrite_output=overwrite_output,
        )

    def run_graph(
        self,
        stream_spec,
        *,
        overwrite_output=False,
        timeout=None,
    ):
        """Run a compiled graph with captured output and no shell."""
        command = self.compile_graph(stream_spec, overwrite_output=overwrite_output)
        return self.run_command(command, timeout=timeout)

    def popen_graph(
        self,
        stream_spec,
        *,
        overwrite_output=False,
        **popen_kwargs,
    ):
        """Start a graph for streaming progress and controlled cancellation."""
        command = self.compile_graph(stream_spec, overwrite_output=overwrite_output)
        environment = popen_kwargs.pop("env", None)
        if environment is None:
            environment = self.subprocess_environment()
        with self._external_launch_context():
            return subprocess.Popen(
                command,
                env=environment,
                shell=False,
                **popen_kwargs,
            )

    def run_command(
        self,
        command,
        *,
        timeout=None,
    ):
        """Run an already-tokenized command under the correct frozen-app environment."""
        with self._external_launch_context():
            return subprocess.run(
                list(command),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=timeout,
                check=False,
                shell=False,
                env=self.subprocess_environment(),
            )

    def probe(self, path):
        """Probe media through ffmpeg-python using the resolved FFprobe binary."""
        # ffmpeg-python 0.2.0 does not expose Popen's env argument in probe().
        # Temporarily sanitizing the frozen runtime environment keeps PATH builds
        # from leaking PyInstaller's native-library search paths into FFprobe.
        with self._external_probe_context():
            result = ffmpeg.probe(str(path), cmd=str(self.ffprobe_path))
        if not isinstance(result, dict):
            raise TypeError("FFprobe returned an unexpected result type.")
        return result

    def validate_installation(self):
        """Verify that both resolved programs launch successfully."""
        for label, executable in (
            ("FFmpeg", self.ffmpeg_path),
            ("FFprobe", self.ffprobe_path),
        ):
            try:
                result = self.run_command(
                    [str(executable), "-version"],
                    timeout=EXECUTABLE_CHECK_TIMEOUT_SECONDS,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                source = "bundled executable" if self.bundled else "PATH executable"
                raise ExecutableNotFoundError(
                    f"{label} could not be started from its {source}: '{executable}'.",
                    details=str(exc),
                ) from exc
            if result.returncode != 0:
                details = result.stderr.decode("utf-8", errors="replace").strip()
                raise ExecutableNotFoundError(
                    f"{label} was found at '{executable}' but failed its startup check.",
                    details=details,
                )

    def subprocess_environment(self):
        """Return an environment safe for bundled or external child processes."""
        environment = dict(os.environ)
        if self.bundled or not getattr(sys, "frozen", False):
            return environment

        # A PATH build must not pass PyInstaller's private native-library paths
        # to a system FFmpeg; compatible names there can shadow FFmpeg's own DLLs.
        bundle_root = _runtime_bundle_root()
        if bundle_root is not None:
            environment["PATH"] = _remove_bundle_paths(
                environment.get("PATH", ""),
                bundle_root,
            )

        for variable in ("LD_LIBRARY_PATH", "LIBPATH", "DYLD_LIBRARY_PATH"):
            original_variable = f"{variable}_ORIG"
            if original_variable in environment:
                environment[variable] = environment[original_variable]
            else:
                environment.pop(variable, None)
        return environment

    @contextlib.contextmanager
    def _external_launch_context(self):
        if self.bundled or not getattr(sys, "frozen", False):
            yield
            return
        with _temporary_windows_dll_directory_reset():
            yield

    @contextlib.contextmanager
    def _external_probe_context(self):
        if self.bundled or not getattr(sys, "frozen", False):
            yield
            return

        # ffmpeg-python's probe() cannot accept an environment, so this is the
        # narrowest scope in which its internally-created process can inherit a
        # sanitized one. The finally block restores the exact prior values.
        sanitized = self.subprocess_environment()
        managed_names = {
            "PATH",
            "LD_LIBRARY_PATH",
            "LIBPATH",
            "DYLD_LIBRARY_PATH",
        }
        with _RUNTIME_ENVIRONMENT_LOCK:
            previous = {name: os.environ.get(name) for name in managed_names}
            try:
                for name in managed_names:
                    if name in sanitized:
                        os.environ[name] = sanitized[name]
                    else:
                        os.environ.pop(name, None)
                with _temporary_windows_dll_directory_reset(lock=False):
                    yield
            finally:
                for name, value in previous.items():
                    if value is None:
                        os.environ.pop(name, None)
                    else:
                        os.environ[name] = value


def resolve_ffmpeg_tools(
    *,
    validate=True,
    bundle_root=None,
    which=shutil.which,
    platform_name=None,
):
    """Resolve an atomic FFmpeg/FFprobe pair and optionally launch-check both."""
    executables = resolve_ffmpeg_executables(
        bundle_root=bundle_root,
        which=which,
        platform_name=platform_name,
    )
    tools = FFmpegTools(executables)
    if validate:
        tools.validate_installation()
    return tools


def resolve_ffmpeg_executables(
    *,
    bundle_root=None,
    which=shutil.which,
    platform_name=None,
):
    """Resolve both bundled tools or both PATH tools without ever mixing them."""
    platform_name = platform_name or sys.platform
    executable_suffix = ".exe" if platform_name == "win32" else ""
    ffmpeg_name = f"ffmpeg{executable_suffix}"
    ffprobe_name = f"ffprobe{executable_suffix}"

    bundle_root = _runtime_bundle_root() if bundle_root is None else Path(bundle_root)
    if bundle_root is not None:
        binary_directory = bundle_root / BUNDLED_BINARY_DIRECTORY
        bundled_ffmpeg = binary_directory / ffmpeg_name
        bundled_ffprobe = binary_directory / ffprobe_name
        ffmpeg_present = bundled_ffmpeg.is_file()
        ffprobe_present = bundled_ffprobe.is_file()
        # Never combine a bundled executable with a PATH executable: mismatched
        # builds can disagree about codecs and metadata even when each launches.
        if ffmpeg_present != ffprobe_present:
            missing = "FFprobe" if ffmpeg_present else "FFmpeg"
            raise ExecutableNotFoundError(
                f"This application bundle is incomplete: bundled {missing} is missing. "
                "Rebuild or reinstall the application."
            )
        if ffmpeg_present and ffprobe_present:
            return FFmpegExecutables(
                ffmpeg_path=bundled_ffmpeg.resolve(),
                ffprobe_path=bundled_ffprobe.resolve(),
                bundled=True,
            )

    ffmpeg_result = which("ffmpeg")
    ffprobe_result = which("ffprobe")
    missing_names = [
        label
        for label, value in (("FFmpeg", ffmpeg_result), ("FFprobe", ffprobe_result))
        if not value
    ]
    if missing_names:
        if len(missing_names) == 2:
            lead = "FFmpeg and FFprobe were not found in PATH."
        else:
            lead = f"{missing_names[0]} was not found in PATH."
        raise ExecutableNotFoundError(
            f"{lead}\n\nInstall FFmpeg and make sure ffmpeg and ffprobe "
            "are both available from your command line."
        )

    return FFmpegExecutables(
        ffmpeg_path=Path(ffmpeg_result).resolve(),
        ffprobe_path=Path(ffprobe_result).resolve(),
        bundled=False,
    )


def _runtime_bundle_root():
    if not getattr(sys, "frozen", False):
        return None
    extraction_directory = getattr(sys, "_MEIPASS", None)
    return Path(extraction_directory) if extraction_directory else None


def _remove_bundle_paths(path_value, bundle_root):
    retained = []
    for entry in path_value.split(os.pathsep):
        if not entry:
            continue
        try:
            candidate = Path(entry).resolve(strict=False)
            candidate.relative_to(bundle_root.resolve(strict=False))
        except (OSError, ValueError):
            retained.append(entry)
    return os.pathsep.join(retained)


@contextlib.contextmanager
def _temporary_windows_dll_directory_reset(*, lock=True):
    """Undo PyInstaller's Windows DLL search override while starting PATH tools."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        yield
        return

    lock_context = _RUNTIME_ENVIRONMENT_LOCK if lock else contextlib.nullcontext()
    with lock_context:
        # SetDllDirectory is process-wide, not a Popen option. Preserve and
        # restore it while the child inherits Windows' normal DLL search order.
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetDllDirectoryW.argtypes = [ctypes.c_uint32, ctypes.c_wchar_p]
        kernel32.GetDllDirectoryW.restype = ctypes.c_uint32
        kernel32.SetDllDirectoryW.argtypes = [ctypes.c_wchar_p]
        kernel32.SetDllDirectoryW.restype = ctypes.c_int
        buffer = ctypes.create_unicode_buffer(32768)
        length = kernel32.GetDllDirectoryW(len(buffer), buffer)
        previous = buffer.value if length else None
        if not kernel32.SetDllDirectoryW(None):
            LOGGER.debug("Could not reset the Windows DLL directory before child launch")
        try:
            yield
        finally:
            kernel32.SetDllDirectoryW(previous)
