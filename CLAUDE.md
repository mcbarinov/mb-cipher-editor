# AI Agent Start Guide

## Critical: Language
RESPOND IN ENGLISH. Always. No exceptions.
User's language does NOT determine your response language.
Only switch if user EXPLICITLY requests it (e.g., "respond in {language}").
Language switching applies ONLY to chat. All code, comments, commit messages, and files must ALWAYS be in English — no exceptions.

## Mandatory Rules (external)
These files are REQUIRED. Read them fully and follow all rules.
- `~/.claude/shared-rules/general.md`
- `~/.claude/shared-rules/python.md`

## Project Reading (context)
These files are REQUIRED for project understanding.
- `README.md`
- `docs/cli-architecture.md`

## Preflight (mandatory)
Before your first response:
1. Read all files listed above.
2. Do not answer until all are read.
3. In your first reply, list every file you have read from this document.

Failure to follow this protocol is considered an error.

## Testing the app

The user may have a real app running. NEVER run `mb-cipher-editor` commands against the default data directory.
For any manual verification, use `--data-dir ~/.claude/scratch/mb-cipher-editor/data-dir`.
This lives under the pre-authorized scratch directory (see `~/.claude/shared-rules/general.md` rule #6),
so you can create, read, and delete files there without asking for permission.
