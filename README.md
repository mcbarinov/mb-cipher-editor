# mb-cipher-editor

CLI + TUI editor for encrypted text files. View and edit plain-text files stored with strong symmetric encryption.

> **Status:** Early scaffolding — no real functionality yet.

## CLI commands

- `mb-cipher-editor hello` — placeholder command used to verify the wiring

## Architecture

General CLI application architecture patterns are described in [docs/cli-architecture.md](docs/cli-architecture.md).

### Core (`core/`)

Central application layer. Holds configuration and business logic. Consumers never import from `core/` directly — they receive a `Core` instance and access everything through it:

- `core.config` — application configuration
- `core.service` — business logic

### Consumers

- **CLI** (`cli/`) — command-line interface. Each command receives `Core` via `CoreContext`.

## Storage

Encrypted files live under the data directory, default `~/.local/mb-cipher-editor/`. Override with `--data-dir` or the `MB_CIPHER_EDITOR_DATA_DIR` environment variable.

## Tech stack

- Python 3.14
- [mm-clikit](https://github.com/mcbarinov/mm-clikit) — CLI toolkit (Typer enhancements, config, logging)
