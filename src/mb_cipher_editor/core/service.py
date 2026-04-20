"""Core business logic."""

from mb_cipher_editor.config import Config


class Service:
    """Main application service."""

    def __init__(self, config: Config) -> None:
        """Initialize with application configuration."""
        self._config = config  # Application configuration

    def hello(self, name: str) -> str:
        """Return a greeting. Placeholder for the real editor logic."""
        target = name.strip() or "world"
        return f"Hello, {target}!"
