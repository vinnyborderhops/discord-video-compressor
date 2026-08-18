from pathlib import Path

import pytest

from build_common import (
    BUNDLED_BINARY_DIRECTORY as BUILD_BINARY_DIRECTORY,
)
from build_common import (
    locate_ffmpeg_binaries,
)
from compressor.ffmpeg_tools import BUNDLED_BINARY_DIRECTORY as RUNTIME_BINARY_DIRECTORY
from compressor.utils import generate_output_path


def test_output_name_collision_sequence(tmp_path):
    input_path = tmp_path / "my video (final).mov"
    first = tmp_path / "my video (final)_compressed.mp4"
    second = tmp_path / "my video (final)_compressed_2.mp4"
    assert generate_output_path(input_path) == first
    first.touch()
    assert generate_output_path(input_path) == second
    second.touch()
    assert generate_output_path(input_path) == (tmp_path / "my video (final)_compressed_3.mp4")


def test_output_path_supports_configured_directory_and_suffix(tmp_path):
    input_path = tmp_path / "source" / "clip.mov"
    output_directory = tmp_path / "compressed"
    output_directory.mkdir()

    assert generate_output_path(
        input_path,
        directory=str(output_directory),
        suffix="_discord",
    ) == (output_directory / "clip_discord.mp4")


def test_specs_differ_only_by_bundle_boolean():
    project_root = Path(__file__).resolve().parents[1]
    bundled = (project_root / "build_bundled.spec").read_text(encoding="utf-8")
    path_build = (project_root / "build_path.spec").read_text(encoding="utf-8")
    assert bundled.count("BUNDLE_FFMPEG = True") == 1
    assert path_build.count("BUNDLE_FFMPEG = False") == 1
    assert bundled.replace("BUNDLE_FFMPEG = True", "BUNDLE_FFMPEG = VALUE") == path_build.replace(
        "BUNDLE_FFMPEG = False", "BUNDLE_FFMPEG = VALUE"
    )


def test_build_and_runtime_bundle_directories_match():
    assert BUILD_BINARY_DIRECTORY == RUNTIME_BINARY_DIRECTORY


def test_bundled_build_requires_canonical_windows_executables(tmp_path):
    ffmpeg_path = tmp_path / "ffmpeg.exe"
    ffprobe_path = tmp_path / "ffprobe.exe"
    ffmpeg_path.touch()
    ffprobe_path.touch()
    requested = []

    def lookup(name):
        requested.append(name)
        return str(ffmpeg_path if name == "ffmpeg.exe" else ffprobe_path)

    binaries = locate_ffmpeg_binaries(platform_name="win32", which=lookup)
    assert requested == ["ffmpeg.exe", "ffprobe.exe"]
    assert binaries == [
        (str(ffmpeg_path.absolute()), BUILD_BINARY_DIRECTORY),
        (str(ffprobe_path.absolute()), BUILD_BINARY_DIRECTORY),
    ]

    wrapper = tmp_path / "ffmpeg.cmd"
    wrapper.touch()
    with pytest.raises(SystemExit):
        locate_ffmpeg_binaries(
            platform_name="win32",
            which=lambda name: str(wrapper if name == "ffmpeg.exe" else ffprobe_path),
        )
