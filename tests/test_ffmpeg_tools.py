import sys
from pathlib import Path

import ffmpeg
import pytest

from compressor.errors import ExecutableNotFoundError
from compressor.ffmpeg_tools import (
    BUNDLED_BINARY_DIRECTORY,
    FFmpegTools,
    resolve_ffmpeg_executables,
)


def test_bundled_pair_wins_without_calling_path_lookup(tmp_path: Path):
    binary_directory = tmp_path / BUNDLED_BINARY_DIRECTORY
    binary_directory.mkdir()
    ffmpeg_path = binary_directory / "ffmpeg.exe"
    ffprobe_path = binary_directory / "ffprobe.exe"
    ffmpeg_path.touch()
    ffprobe_path.touch()

    def forbidden_lookup(_name):
        pytest.fail("PATH lookup must not occur for a complete bundle")

    resolved = resolve_ffmpeg_executables(
        bundle_root=tmp_path,
        which=forbidden_lookup,
        platform_name="win32",
    )

    assert resolved.bundled is True
    assert resolved.ffmpeg_path == ffmpeg_path.resolve()


def test_partial_bundle_is_rejected_instead_of_mixed_with_path(tmp_path: Path):
    binary_directory = tmp_path / BUNDLED_BINARY_DIRECTORY
    binary_directory.mkdir()
    (binary_directory / "ffmpeg.exe").touch()

    with pytest.raises(ExecutableNotFoundError):
        resolve_ffmpeg_executables(
            bundle_root=tmp_path,
            which=lambda name: f"C:/system/{name}.exe",
            platform_name="win32",
        )


def test_actual_frozen_meipass_is_used(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    binary_directory = tmp_path / BUNDLED_BINARY_DIRECTORY
    binary_directory.mkdir()
    (binary_directory / "ffmpeg.exe").touch()
    (binary_directory / "ffprobe.exe").touch()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

    resolved = resolve_ffmpeg_executables(
        which=lambda _name: pytest.fail("PATH lookup must not occur"),
        platform_name="win32",
    )

    assert resolved.bundled is True


def test_path_pair_and_paths_with_spaces(tmp_path: Path):
    directory = tmp_path / "ffmpeg tools"
    directory.mkdir()
    ffmpeg_path = directory / "ffmpeg executable"
    ffprobe_path = directory / "ffprobe executable"
    ffmpeg_path.touch()
    ffprobe_path.touch()

    def lookup(name):
        return str(ffmpeg_path if name == "ffmpeg" else ffprobe_path)

    resolved = resolve_ffmpeg_executables(
        bundle_root=directory / "empty-bundle",
        which=lookup,
        platform_name="linux",
    )
    tools = FFmpegTools(resolved)
    graph = ffmpeg.output(
        ffmpeg.input("color=s=16x16", f="lavfi"),
        "pipe:",
        f="null",
    )

    command = tools.compile_graph(graph)

    assert command[0] == str(ffmpeg_path.resolve())


def test_missing_path_tool_has_actionable_error():
    with pytest.raises(ExecutableNotFoundError, match="FFprobe was not found"):
        resolve_ffmpeg_executables(
            bundle_root=Path("missing-bundle"),
            which=lambda name: "C:/ffmpeg.exe" if name == "ffmpeg" else None,
            platform_name="win32",
        )


def test_probe_receives_resolved_ffprobe_path(monkeypatch: pytest.MonkeyPatch):
    executables = resolve_ffmpeg_executables(
        bundle_root=Path("missing-bundle"),
        which=lambda name: f"C:/Tools With Spaces/{name}.exe",
        platform_name="win32",
    )
    tools = FFmpegTools(executables)
    probe_calls = []

    def probe(path, **kwargs):
        probe_calls.append((path, kwargs))
        return {"streams": [], "format": {}}

    monkeypatch.setattr("compressor.ffmpeg_tools.ffmpeg.probe", probe)

    tools.probe(Path("C:/Videos/input file.mp4"))

    assert probe_calls == [
        (
            str(Path("C:/Videos/input file.mp4")),
            {"cmd": str(executables.ffprobe_path)},
        )
    ]
