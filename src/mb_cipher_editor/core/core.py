"""Composition root — holds config and service layer."""

import logging

from mm_clikit import setup_logging

from mb_cipher_editor.config import Config
from mb_cipher_editor.core.service import Service


class Core:
    """Application composition root. Creates and owns all shared resources."""

    def __init__(self, config: Config) -> None:
        """Initialize core with configuration."""
        self.config = config  # Application configuration
        # console_level=None keeps stdout clean for plain CLI output and TUI mode;
        # user-facing errors go through CliError/typer independently of stdlib logging.
        setup_logging(
            "mb_cipher_editor",
            file_path=config.log_path,
            file_level=logging.DEBUG if config.debug else logging.INFO,
            console_level=None,
        )

        self.service = Service(config)  # Business logic

    def close(self) -> None:
        """Release resources."""
