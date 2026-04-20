"""Typed CLI context."""

import typer
from mm_clikit import CoreContext
from mm_clikit import use_context as _use_context

from mb_cipher_editor.core.core import Core


def use_context(ctx: typer.Context) -> CoreContext[Core, None]:
    """Extract typed core context from Typer context."""
    return _use_context(ctx, CoreContext[Core, None])
