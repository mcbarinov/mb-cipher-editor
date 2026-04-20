"""Placeholder hello command — smoke test for the CLI wiring."""

from typing import Annotated

import typer
from mm_clikit import print_plain

from mb_cipher_editor.cli.context import use_context


def hello(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Name to greet.")] = "world",
) -> None:
    """Print a greeting. Used to verify the CLI is wired up correctly."""
    app = use_context(ctx)
    print_plain(app.core.service.hello(name))
