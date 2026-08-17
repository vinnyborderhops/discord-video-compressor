"""Allow ``python -m compressor`` to behave like the executable."""

from compressor.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
