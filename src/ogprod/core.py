"""Core logic: pure functions, independent of any user interface."""


def greet(name: str = "world") -> str:
    """Return a greeting for `name`."""
    return f"Hello, {name}!"
