# Discord Video Compressor

A Python CLI for compressing videos toward a target file size with automatic hardware acceleration, bitrate-aware downscaling, progress reporting, and safe output handling.

The compressor probes the source with FFprobe, calculates a video/audio bitrate budget, validates the best available encoder, and produces a broadly compatible H.264/AAC MP4.

## Features

* Configurable target size (`20 MB` by default)
* H.264 video with AAC audio when audio is present
* Automatic encoder detection using real initialization tests

  * NVIDIA NVENC (`h264_nvenc`)
  * AMD AMF (`h264_amf`)
  * Intel Quick Sync (`h264_qsv`)
  * Apple VideoToolbox (`h264_videotoolbox`)
  * CPU fallback (`libx264`)
* Persistent per-user encoder cache with validation before reuse
* Temporary per-run encoder overrides
* Bitrate-aware resolution downscaling without upscaling
* Terminal progress reporting
* MP4 `faststart` metadata
* Same-directory temporary encodes and validated final publication
* No-overwrite output behavior and filename collision avoidance
* Cleanup after failed or interrupted encodes
* Normal, verbose, and debug output modes
* Windows drag-and-drop support for built executables
* PATH-based and FFmpeg-bundled PyInstaller builds
* Pytest test suite and Ruff configuration

## Requirements

* Python 3.10+ recommended
* [`ffmpeg-python`](https://github.com/kkroening/ffmpeg-python)
* FFmpeg and FFprobe when running from source or using the PATH build

Install the Python dependencies:

```bash
python -m pip install -r requirements.txt
```

Verify FFmpeg and FFprobe:

```bash
ffmpeg -version
ffprobe -version
```

The bundled executable build includes the FFmpeg/FFprobe pair found on the build machine, so those tools do not need to be installed separately on the target machine.

## Installation

Create a virtual environment:

```bash
python -m venv .venv
```

### Windows PowerShell

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

### Windows Command Prompt

```batch
.\.venv\Scripts\activate.bat
python -m pip install -r requirements.txt
```

### macOS / Linux

```bash
source .venv/bin/activate
python -m pip install -r requirements.txt
```

For development tools:

```bash
python -m pip install -r requirements-optional.txt
```

## Usage

Compress a video using the default `20 MB` target:

```bash
python main.py video.mp4
```

The package entry point is equivalent:

```bash
python -m compressor video.mp4
```

### Set the target size

```bash
python main.py video.mp4 --target-size 10
```

Short form:

```bash
python main.py video.mp4 -t 10
```

### Choose the output path

```bash
python main.py video.mp4 --output compressed.mp4
```

Without `--output`, the compressor creates `INPUT_compressed.mp4`. If that path already exists, it selects a collision-safe filename instead of overwriting it.

### Force an encoder for one run

```bash
python main.py video.mp4 --encoder nvidia
```

Valid values are `nvidia`, `amd`, `intel`, `mac`, and `cpu`.

Manual overrides are validated before use and do not replace the automatically cached preference.

### Inspect or redetect the encoder

```bash
python main.py --show-encoder
python main.py --redetect-encoder
```

### Diagnostics

```bash
python main.py video.mp4 --verbose
python main.py video.mp4 --debug
```

View all CLI options:

```bash
python main.py --help
```

## Settings

`settings.json` is created with defaults on first launch. Find, open, or reset it with:

```bash
python main.py --show-config
python main.py --open-config
python main.py --reset-config
```

Default settings:

```json
{
  "schema_version": 1,
  "target_size_mb": 20.0,
  "encoder": "auto",
  "output": {
    "directory": "source",
    "suffix": "_compressed"
  },
  "quality": {
    "auto_downscale": true,
    "target_bits_per_pixel": 0.075,
    "minimum_dimension": 128
  },
  "audio": {
    "minimum_bitrate_kbps": 64.0,
    "maximum_bitrate_kbps": 96.0
  },
  "console": {
    "verbose": false,
    "pause_on_exit": true
  }
}
```

Settings locations:

* Windows: `%LOCALAPPDATA%\DiscordVideoCompressor\settings.json`
* macOS: `~/Library/Application Support/DiscordVideoCompressor/settings.json`
* Linux: `${XDG_CONFIG_HOME:-~/.config}/discord-video-compressor/settings.json`

`encoder` accepts `auto`, `nvidia`, `amd`, `intel`, `mac`, or `cpu`. `output.directory` accepts `source` or a directory path. Existing outputs are never overwritten.

CLI options override settings. `--target-size` overrides `target_size_mb`; `--encoder` overrides `encoder`; `--verbose` and `--debug` override normal console verbosity.

## Windows Drag and Drop

A built Windows executable can be used by dragging a video directly onto `DiscordVideoCompressor.exe`.

The dropped file uses `settings.json` for target size, encoder, output naming, quality, audio, and console behavior. `pause_on_exit` keeps an Explorer-launched console open so errors and results remain visible.

## Encoder Selection

Automatic detection uses this priority order:

| Priority | Type   | FFmpeg encoder      |
| -------: | ------ | ------------------- |
|        1 | NVIDIA | `h264_nvenc`        |
|        2 | AMD    | `h264_amf`          |
|        3 | Intel  | `h264_qsv`          |
|        4 | Apple  | `h264_videotoolbox` |
|        5 | CPU    | `libx264`           |

The application does not select an encoder based only on detected hardware. Each candidate performs a small real encode using the production option profile. The first encoder that successfully initializes is selected.

The automatic result is cached per user, but the cached encoder is initialization-tested again before reuse so driver or hardware changes do not permanently leave a stale selection.

## How Compression Works

At a high level, each run:

1. Resolves and validates one matching FFmpeg/FFprobe pair.
2. Probes the source video.
3. Calculates a bitrate budget from video duration and requested size.
4. Allocates AAC audio bitrate when audio is present.
5. Assigns the remaining bitrate to H.264 video.
6. Reduces resolution when the available bitrate is too low for the source resolution.
7. Encodes to a temporary MP4 while reporting progress.
8. Validates the completed temporary output.
9. Publishes the final file without silently overwriting another file.
10. Reports the final size, size reduction, encoder, encode time, and target-size status.

The compressor favors a useful bitrate-per-pixel level over preserving source resolution at any cost.

## Building Executables

The repository contains two PyInstaller builds.

### PATH build

```bash
python -m PyInstaller --noconfirm --clean build_path.spec
```

This build resolves FFmpeg and FFprobe from the target machine's `PATH` at runtime.

### Bundled FFmpeg build

```bash
python -m PyInstaller --noconfirm --clean build_bundled.spec
```

This build locates `ffmpeg` and `ffprobe` on the build machine and embeds the pair in the executable.

Build output is written under `dist/`.

Both variants pass `icon.ico` to PyInstaller, which embeds it in the Windows executable.
The macOS output is a console binary rather than an `.app`, and Linux desktop launchers
keep their icons separately from the executable, so those two artifacts do not display an
embedded application icon.

### GitHub Actions builds

The `Build executables` workflow runs on Windows x64, Linux x64, and macOS Intel. Each
runner creates the PATH variant first, downloads a standalone FFmpeg/FFprobe pair, creates
the bundled variant, smoke-tests both, and uploads one package containing both executables.

Run it from the repository's **Actions** tab to create artifacts retained for 14 days. Pushing
a tag such as `v1.0.0` also creates a GitHub Release with generated release notes and attaches
all three platform packages. Re-running that tag workflow replaces same-named release assets.
The macOS build uses GitHub's Intel runner because the upstream macOS static FFmpeg downloads
used by the workflow are x86-64 builds.

## Development

Install the optional development dependencies:

```bash
python -m pip install -r requirements-optional.txt
```

Run the test suite:

```bash
python -m pytest
```

Lint and check formatting:

```bash
python -m ruff check .
python -m ruff format --check .
```

Format the project:

```bash
python -m ruff format .
```

The repository also includes VS Code settings, recommended extensions, run/test tasks, and both PyInstaller build tasks under `.vscode/`.

## Troubleshooting

For FFmpeg or FFprobe issues:

```bash
ffmpeg -version
ffprobe -version
```

For encoder-selection issues:

```bash
python main.py --show-encoder
python main.py --redetect-encoder
```

For detailed compression or encoder diagnostics:

```bash
python main.py video.mp4 --debug
```

If the final file is slightly larger than the target, use a slightly smaller requested size when an upload service enforces a strict hard limit.

## License

This project is licensed under the GNU General Public License v3.0. See [`LICENSE`](LICENSE) for the full license text.
