"""Pure helpers for the macOS overlay installer."""
from pathlib import Path


def resolve_binary_source(binary_source, compiled):
    """Choose where the overlay executable comes from.

    A packaged .app ships a prebuilt universal binary. A source checkout falls
    back to compiling the Swift sources at install time.
    """
    if binary_source is not None:
        path = Path(binary_source)
        if path.is_file():
            return ("prebuilt", path)
    return ("compile", Path(compiled))
