"""Small filesystem and presentation helpers."""

import os
from pathlib import Path


def path_is_occupied(path):
    """Return true for files, directories, and broken symbolic links."""
    return os.path.lexists(path)


def generate_output_path(input_path):
    """Generate a non-colliding ``*_compressed.mp4`` path beside the input."""
    input_path = Path(input_path)
    base = input_path.with_name(f"{input_path.stem}_compressed.mp4")
    if not path_is_occupied(base):
        return base

    counter = 2
    while True:
        candidate = input_path.with_name(f"{input_path.stem}_compressed_{counter}.mp4")
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
