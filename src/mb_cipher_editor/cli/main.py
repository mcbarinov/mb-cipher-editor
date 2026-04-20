"""CLI app definition and initialization."""

from pathlib import Path
from typing import Annotated

import typer
from mm_clikit import CoreContext, TyperPlus

from mb_cipher_editor.cli.commands.hello import hello
from mb_cipher_editor.config import Config
from mb_cipher_editor.core.core import Core

app = TyperPlus(package_name="mb-cipher-editor", json_option=False)


@app.callback()
def main(
    ctx: typer.Context,
    *,
    data_dir: Annotated[
        Path | None,
        typer.Option("--data-dir", help="Data directory. Env: MB_CIPHER_EDITOR_DATA_DIR."),
    ] = None,
    debug: Annotated[bool, typer.Option("--debug", help="Enable DEBUG level in the log file.")] = False,
) -> None:
    """CLI + TUI editor for encrypted text files."""
    config = Config.build(data_dir, debug=debug)
    core = Core(config)
    ctx.call_on_close(core.close)
    ctx.obj = CoreContext[Core](core=core, out=None)


app.command()(hello)
