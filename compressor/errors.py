"""Application-specific exceptions with user-facing messages."""


class CompressorError(Exception):
    """Base class for expected failures that should not show a traceback."""

    def __init__(
        self,
        message,
        *,
        details=None,
        exit_code=1,
    ):
        super().__init__(message)
        self.details = details
        self.exit_code = exit_code


class ValidationError(CompressorError):
    """Raised when user input is invalid."""


class ExecutableNotFoundError(CompressorError):
    """Raised when FFmpeg or FFprobe cannot be resolved or executed."""


class ConfigurationError(CompressorError):
    """Raised when persistent configuration cannot be written."""


class ProbeError(CompressorError):
    """Raised when a media file cannot be probed or parsed."""


class EncoderUnavailableError(CompressorError):
    """Raised when no requested/supported encoder can initialize."""


class OutputExistsError(CompressorError):
    """Raised when an operation would overwrite an existing output."""


class CompressionError(CompressorError):
    """Raised when FFmpeg cannot produce a valid output."""


class InterruptedCompressionError(CompressorError):
    """Raised after a user interruption and process cleanup."""

    def __init__(self, message="Compression was interrupted."):
        super().__init__(message, exit_code=130)
