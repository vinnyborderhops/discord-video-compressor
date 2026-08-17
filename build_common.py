"""Shared PyInstaller build definition for both executable variants."""

import shutil
import sys
from pathlib import Path

from PyInstaller.building.api import EXE, PYZ
from PyInstaller.building.build_main import Analysis

PROJECT_ROOT = Path(__file__).resolve().parent
ENTRY_POINT = PROJECT_ROOT / "main.py"
APPLICATION_NAME = "DiscordVideoCompressor"
BUNDLED_BINARY_DIRECTORY = "ffmpeg-bin"


def locate_ffmpeg_binaries(
    *,
    platform_name=None,
    which=shutil.which,
):
    """Find the build machine's FFmpeg pair or fail the bundled build clearly."""
    platform_name = platform_name or sys.platform
    suffix = ".exe" if platform_name == "win32" else ""
    ffmpeg_name = f"ffmpeg{suffix}"
    ffprobe_name = f"ffprobe{suffix}"
    ffmpeg_path = which(ffmpeg_name)
    ffprobe_path = which(ffprobe_name)
    missing = [
        name
        for name, value in (("ffmpeg", ffmpeg_path), ("ffprobe", ffprobe_path))
        if value is None
    ]
    if missing:
        missing_text = " and ".join(missing)
        raise SystemExit(
            "Cannot build the bundled variant because "
            f"{missing_text} could not be found on the build machine's PATH.\n"
            "Install FFmpeg and confirm both ffmpeg and ffprobe run from this shell."
        )

    # Guaranteed by the missing-pair check above; the assertions also make that
    # narrowing explicit to type checkers without changing the user-facing error.
    assert ffmpeg_path is not None
    assert ffprobe_path is not None
    ffmpeg_source = Path(ffmpeg_path).absolute()
    ffprobe_source = Path(ffprobe_path).absolute()
    wrong_names = (
        ffmpeg_source.name.lower() != ffmpeg_name.lower()
        or ffprobe_source.name.lower() != ffprobe_name.lower()
    )
    if wrong_names or not ffmpeg_source.is_file() or not ffprobe_source.is_file():
        raise SystemExit(
            "Cannot build the bundled variant because PATH did not resolve to the "
            f"required {ffmpeg_name} and {ffprobe_name} executable files.\n"
            "Remove command wrappers or broken links that shadow the real FFmpeg tools."
        )

    return [
        (str(ffmpeg_source), BUNDLED_BINARY_DIRECTORY),
        (str(ffprobe_source), BUNDLED_BINARY_DIRECTORY),
    ]


def build_application(*, bundle_ffmpeg):
    """Construct the one-file executable; only the binary list varies by build."""
    binaries = locate_ffmpeg_binaries() if bundle_ffmpeg else []
    analysis = Analysis(
        [str(ENTRY_POINT)],
        pathex=[str(PROJECT_ROOT)],
        binaries=binaries,
        datas=[],
        hiddenimports=[],
        hookspath=[],
        hooksconfig={},
        runtime_hooks=[],
        excludes=[],
        noarchive=False,
        optimize=1,
    )
    python_archive = PYZ(analysis.pure)
    return EXE(
        python_archive,
        analysis.scripts,
        analysis.binaries,
        analysis.datas,
        [],
        name=APPLICATION_NAME,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=True,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )
