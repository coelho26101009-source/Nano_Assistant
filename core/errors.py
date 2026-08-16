"""Custom exception hierarchy for Nano Assistant.

All project‑specific errors inherit from ``NanoError`` so they can be caught
uniformly by higher‑level code.  Keeping the definitions in a dedicated module
simplifies imports and makes future extensions straightforward.
"""

class NanoError(Exception):
    """Base class for all custom errors in Nano Assistant."""

class ToolExecutionError(NanoError):
    """Raised when a tool fails to execute correctly."""

class GuardrailError(NanoError):
    """Raised when a guardrail blocks execution or a confirmation fails."""
