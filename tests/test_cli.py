import sys

import pytest

from compressor import cli


def test_main_uses_process_arguments_when_argv_is_omitted(monkeypatch):
    input_path = r"C:\Videos\replay with spaces.mp4"
    captured = []

    class ParsingStopped(Exception):
        pass

    class RecordingParser:
        def parse_args(self, argv):
            captured.extend(argv)
            raise ParsingStopped

    monkeypatch.setattr(sys, "argv", ["DiscordVideoCompressor.exe", input_path])
    monkeypatch.setattr(cli, "build_parser", RecordingParser)

    with pytest.raises(ParsingStopped):
        cli.main()

    assert captured == [input_path]
