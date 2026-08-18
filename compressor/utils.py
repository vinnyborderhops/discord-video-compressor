"""Small filesystem and presentation helpers."""

import os
from pathlib import Path


def path_is_occupied(path):
    """Return true for files, directories, and broken symbolic links."""
    return os.path.lexists(path)


def generate_output_path(input_path, *, directory="source", suffix="_compressed"):
    """Generate a non-colliding configured MP4 output path."""
    input_path = Path(input_path)
    output_directory = input_path.parent if directory == "source" else Path(directory).expanduser()
    base = output_directory / f"{input_path.stem}{suffix}.mp4"
    if not path_is_occupied(base):
        return base

    counter = 2
    while True:
        candidate = output_directory / f"{input_path.stem}{suffix}_{counter}.mp4"
        if not path_is_occupied(candidate):
            return candidate
        counter += 1


def paths_refer_to_same_location(first, second):
    """Compare existing or prospective paths without requiring both to exist."""
    try:
        return first.samefile(second)
    except (FileNotFoundError, OSError):
        first_normalized = os.path.normcase(str(first.expanduser().resolve(strict=False)))
        second_normalized = os.path.normcase(str(second.expanduser().resolve(strict=False)))
        return first_normalized == second_normalized


def format_bytes(byte_count):
    """Format a byte count with binary units."""
    value = float(byte_count)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(value) < 1024.0 or unit == "TiB":
            return f"{value:.2f} {unit}"
        value /= 1024.0
    return f"{value:.2f} TiB"
