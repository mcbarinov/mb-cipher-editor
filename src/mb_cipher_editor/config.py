"""Application settings and user-facing configuration."""

from __future__ import annotations

from functools import cached_property
from pathlib import Path
from typing import ClassVar

from mm_clikit import BaseDataDirConfig
from pydantic import ConfigDict, Field, computed_field


class Config(BaseDataDirConfig):
    """Top-level application configuration."""

    app_name: ClassVar[str] = "mb-cipher-editor"

    model_config = ConfigDict(frozen=True, extra="forbid")

    debug: bool = Field(default=False, description="Enable DEBUG level in the log file")

    @computed_field
    @cached_property
    def log_path(self) -> Path:
        """Rotating log file path."""
        return self.data_dir / "cipher-editor.log"

    def base_argv(self) -> list[str]:
        """Extend inherited argv with --debug when set."""
        args = super().base_argv()
        if self.debug:
            args.append("--debug")
        return args

    @staticmethod
    def build(data_dir: Path | None = None, *, debug: bool = False) -> Config:
        """Build a Config from CLI arg / env var / default."""
        resolved = Config.resolve_data_dir(data_dir)
        return Config(data_dir=resolved, debug=debug)
