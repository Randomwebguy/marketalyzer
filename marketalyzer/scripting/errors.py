"""Errors raised while parsing or running a script."""

from __future__ import annotations


class ScriptError(Exception):
    """A problem in a script, reported with its position for the editor.

    Parameters
    ----------
    message
        What went wrong, in Turkish, for the person editing the script.
    line, col
        1-based position of the problem, when known.
    """

    def __init__(self, message: str, line: int | None = None, col: int | None = None):
        super().__init__(message)
        self.message = message
        self.line = line
        self.col = col

    def __str__(self) -> str:
        """Return the message with its line number."""
        if self.line is None:
            return self.message
        return f"Satır {self.line}: {self.message}"

    def to_dict(self) -> dict:
        """Return the error as a JSON-friendly dict."""
        return {"message": self.message, "line": self.line, "col": self.col}

    def located(self, line: int | None, col: int | None) -> ScriptError:
        """Fill in a position if the error does not have one yet."""
        if self.line is None:
            self.line, self.col = line, col
        return self
