"""Exceptions raised by the Syntec ladder SDK."""


class LadError(Exception):
    """Base class for all SDK errors."""


class LadFormatError(LadError):
    """The input is not a structurally valid Syntec ladder file."""


class LadValidationError(LadError):
    """A plan, change document, or generated file failed validation."""


class LadUnsupportedError(LadValidationError):
    """The requested instruction or layout is outside the supported MVP."""


class LadPatchError(LadValidationError):
    """A patch target is invalid, stale, or unsafe to modify."""


class NativeValidationError(LadValidationError):
    """The native LadDLL syntax checker is unavailable or rejected the file."""
