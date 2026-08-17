"""Executable entry point for Discord Video Compressor."""

import sys


def _load_cli():
    try:
        from compressor.cli import main
    except ImportError as exc:
        # Only translate the optional wrapper's missing dependency. Re-raise
        # imports from inside the application so real packaging bugs stay visible.
        if exc.name == "ffmpeg":
            print(
                "The Python package 'ffmpeg-python' is not installed.\n"
                "Install the project requirements with: pip install -r requirements.txt",
                file=sys.stderr,
            )
            raise SystemExit(1) from exc
        raise
    return main


if __name__ == "__main__":
    cli_main = _load_cli()
    raise SystemExit(cli_main())
