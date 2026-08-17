# Discord Video Compressor

A production-oriented Python command-line application that compresses a video to a
user-selected target file size. It produces a broadly compatible MP4 containing H.264
video and, when the source has audio, AAC audio.

The application uses `ffmpeg-python` to build commands and always invokes one centrally
resolved FFmpeg/FFprobe pair. It supports automatic hardware acceleration, persistent
encoder caching, progress reporting, safe temporary outputs, drag-and-drop use on Windows,
and two synchronized PyInstaller builds.

## Highlights

- Configurable target size (20 MB by default)
- NVIDIA NVENC (`h264_nvenc`)
- AMD AMF (`h264_amf`)
- Intel Quick Sync (`h264_qsv`)
- Apple VideoToolbox (`h264_videotoolbox`)
- CPU fallback (`libx264`)
- Real encoder initialization tests rather than GPU-vendor guesses
- Persistent per-user automatic encoder cache
- FFprobe metadata normalization and output validation
- Same-directory temporary encode and no-overwrite final publication
- MP4 fast-start metadata for easier sharing/streaming
- Useful normal output and detailed `--debug` diagnostics
- One-file PyInstaller builds with or without bundled FFmpeg

## Requirements

- Python 3.10 or newer when running from source
- Dependencies from `requirements.txt`
- FFmpeg and FFprobe:
  - on `PATH` when running from source or using the PATH executable build; or
  - on the build machine's `PATH` while creating the bundled executable

`ffmpeg-python` is a Python wrapper; it does not itself install FFmpeg.

## Install

Create and activate a virtual environment, then install the requirements:

```bash
python -m venv .venv
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Windows Command Prompt:
```batch
.\.venv\Scripts\Activate.bat
python -m pip install -r requirements.txt
```

macOS/Linux:

```bash
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Confirm the external programs are visible when using the source or PATH build:

```bash
ffmpeg -version
ffprobe -version
```

## Run from source

```bash
python main.py video.mp4
python main.py video.mp4 --target-size 10
python main.py video.mp4 --output compressed.mp4
python main.py video.mp4 --encoder nvidia
python main.py video.mp4 --encoder cpu
python main.py video.mp4 --redetect-encoder
python main.py --show-encoder
python main.py --show-encoder --redetect-encoder
python main.py video.mp4 --debug
```

You can also run the package:

```bash
python -m compressor video.mp4
```

Use `python main.py --help` for the complete CLI reference.

Manual `--encoder` selection validates the requested encoder for that run. It does not read,
replace, or otherwise alter the cached automatic preference.

## Automatic encoder detection and caching

The application does not detect every encoder on every launch.

On the first automatic run, when the cache is absent or invalid, it performs a short real
encode with each supported encoder. The test uses a tiny generated color source, the null
muxer, and the same advanced quality profile used by real compression. It therefore verifies
that all of the following work together:

- the FFmpeg build contains the encoder;
- the required driver/runtime is installed;
- the hardware can initialize the encoder; and
- the configured quality options are accepted.

The best working encoder is selected in this order:

1. NVIDIA NVENC
2. AMD AMF
3. Intel Quick Sync
4. Apple VideoToolbox
5. CPU/libx264

The selected logical type and exact FFmpeg codec are saved atomically in `config.json`, for
example:

```json
{
  "encoder": "h264_nvenc",
  "preferred_encoder": "nvidia",
  "schema_version": 1,
  "validated_at": "2026-01-01T12:00:00+00:00"
}
```

On later launches, only that one cached encoder receives the small initialization test. If it
works, it is used immediately and the full scan is skipped. If the cache is missing, corrupt,
uses an old schema, contains a mismatched encoder pair, or its encoder no longer initializes,
the full detection runs and a successful result replaces the cache.

Force a full scan and cache update with:

```bash
compressor video.mp4 --redetect-encoder
```

or redetect without compressing:

```bash
compressor --show-encoder --redetect-encoder
```

### Cache location

- Windows: `%LOCALAPPDATA%\DiscordVideoCompressor\config.json`
- macOS: `~/Library/Application Support/DiscordVideoCompressor/config.json`
- Linux: `${XDG_CONFIG_HOME:-~/.config}/discord-video-compressor/config.json`

If configuration storage is temporarily unwritable, compression can still continue, but a
warning explains that detection will be needed on the next launch.

## Encoder quality settings

The quality profiles include NVENC P7/full-resolution
multipass and AQ, AMF preanalysis/lookahead/adaptive mini-GOP settings, QSV lookahead/MBBRC/RDO,
VideoToolbox offline mode, and libx264's slow preset. The bitrate-specific `b:v`, `maxrate`, and
`bufsize` behavior is also preserved.

These advanced options require compatible FFmpeg, drivers, runtimes, and hardware. If a build
or driver rejects an option, the application does not silently weaken the profile. The encoder
initialization test marks that candidate incompatible, records FFmpeg's explanation under
`--debug`, and automatic selection falls back to the next working encoder. Updating FFmpeg and
the graphics driver is the preferred fix.

H.264 4:2:0 formats require even dimensions. For an odd-sized source, the actual compression
graph pads the right or bottom edge by at most one pixel before the preserved `yuv420p`/`nv12`
format filter. This prevents an otherwise obscure hardware-encoder failure without scaling the
picture.

## Target-size calculation

The usable total bitrate is:

```text
target_size_mb * 8 * 1024 * 0.97 / duration_seconds
```

The `0.97` efficiency factor leaves approximately three percent for MP4 container overhead. When
audio is present, it receives 15% of the total constrained to 64-96 kbps, and video receives the
remainder. Silent inputs allocate the full usable budget to video and do not gain a synthetic
audio stream.

Hardware and software encoders use one-pass constrained VBR, so exact byte-level sizing cannot
be guaranteed. The completion summary explicitly reports whether the final file met the target.
Very small targets can produce visibly poor video; the program warns below 250 kbps and rejects
a target that leaves no positive video budget.

## Output safety and drag-and-drop

By default, `video.ext` is written beside its source as `video_compressed.mp4`. If that exists,
the next free name is used (`video_compressed_2.mp4`, and so on).

An explicit existing output is never overwritten. The source can never be the output. Encoding
occurs under a randomized `.part-....mp4` name in the destination directory. Only a successful,
non-empty, FFprobe-validated result is published to the final path. Failed and interrupted
encodes clean up the temporary file.

On Windows, drag a video onto `DiscordVideoCompressor.exe`. Windows supplies the dropped path in
`sys.argv`, so it follows the same validation, collision avoidance, encoder selection, and
progress flow as the normal CLI. Paths with spaces, Unicode, apostrophes, and parentheses are
passed as argv values; the application never constructs a shell command or uses `shell=True`.

## PyInstaller builds

Both builds use the same entry point, executable name, Analysis/PYZ/EXE settings, hidden imports,
data list, optimization, console setting, and UPX setting from `build_common.py`. The two SPEC
files differ only in the `BUNDLE_FFMPEG` Boolean. This keeps them from drifting apart.

Build artifacts are written under `dist/` by PyInstaller.

### Bundled build

```bash
pyinstaller build_bundled.spec
```

This build uses `shutil.which` with the platform's canonical executable names (`ffmpeg.exe` and
`ffprobe.exe` on Windows). It fails immediately with an actionable message if either is missing
or resolves to a command wrapper instead of the required executable. Both programs are added
under the bundle's `ffmpeg-bin` directory.

At runtime, a frozen application checks `sys._MEIPASS/ffmpeg-bin` and uses the bundled pair only
when both files exist. A partial bundle is rejected rather than mixing one bundled program with
one system program. The resolved absolute paths are supplied to every graph compilation and
probe call.

> Most official Windows FFmpeg distributions are self-contained. If your FFmpeg executables
> depend on separate shared libraries, use a static distribution or extend the build binary list
> to package those vendor-specific libraries too.

### PATH build

```bash
pyinstaller build_path.spec
```

This build does not package FFmpeg, FFprobe, or substitutes. At startup it resolves both through
the user's `PATH` and launch-checks each with `-version`. If either is missing, the program asks
the user to install FFmpeg and make both commands available.

When a frozen PATH build launches external tools, the centralized process layer removes
PyInstaller-only native-library search paths for the child process. This avoids loading bundled
Python DLLs/shared libraries into a system FFmpeg process.

## Tests

The core logic is designed for dependency injection and can be tested without encoding a large
video:

```bash
python -m pip install -r requirements-optional.txt
python -m pytest -v
```

Tests cover bitrate calculation, option generation, cache loading and corruption, cached
validation, automatic selection, manual override isolation, executable resolution, custom
FFmpeg paths containing spaces, FFprobe parsing, output naming, and SPEC synchronization.

For a local integration test, create a short sample and compress it with the CPU encoder:

```bash
ffmpeg -f lavfi -i testsrc2=size=640x360:rate=30 \
  -f lavfi -i sine=frequency=1000 -t 3 -c:v libx264 -c:a aac sample.mp4
python main.py sample.mp4 --target-size 2 --encoder cpu --debug
```

PowerShell uses a backtick for command continuation instead of `\`.

## Troubleshooting

### FFmpeg or FFprobe was not found

Run both commands in the same terminal used to start/build the application:

```bash
ffmpeg -version
ffprobe -version
```

If either fails, install FFmpeg and add its `bin` directory to `PATH`, then open a new terminal.
Alternatively, distribute the bundled build.

### A hardware encoder is skipped

Use `--debug --redetect-encoder`. Common causes are an FFmpeg build without the encoder, an old
or missing graphics driver/runtime, unsupported hardware, or a driver/FFmpeg combination that
does not accept one of the required advanced quality options.

### A cached encoder stopped working

Normal startup validates it and automatically redetects on failure. You can request that flow
explicitly with `--redetect-encoder`. Deleting `config.json` also produces a first-run scan, but
is normally unnecessary.

### The output is over the target

The three-percent overhead reserve handles typical MP4 overhead, but constrained VBR encoders can
overshoot on difficult material or very short clips. Try a slightly smaller requested target.
The result summary always reports the actual size and target status.

### The process was interrupted

The application sends FFmpeg a graceful interrupt, escalates to termination if needed, waits for
the child and its output readers, and removes the temporary output. The source is never modified.

## Project layout

```text
.
├── main.py
├── compressor/
│   ├── __init__.py
│   ├── __main__.py
│   ├── bitrate.py
│   ├── cli.py
│   ├── compression.py
│   ├── config.py
│   ├── encoders.py
│   ├── errors.py
│   ├── ffmpeg_tools.py
│   ├── models.py
│   ├── probe.py
│   └── utils.py
├── tests/
├── build_common.py
├── build_bundled.spec
├── build_path.spec
├── requirements.txt
├── pyproject.toml
└── .gitignore
```
