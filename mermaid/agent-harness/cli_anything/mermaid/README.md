# cli-anything-mermaid

A CLI harness for **Mermaid Live Editor** that lets agents create Mermaid state files, inspect diagram source, generate share URLs, and render diagrams through the official Mermaid renderer path.

## Prerequisites

- Python 3.10+
- Internet access to `https://mermaid.live` and `https://mermaid.ink`, or a compatible self-hosted Mermaid renderer service

The upstream Mermaid Live Editor defaults to `mermaid.ink` for render links. This harness uses the same serialized state format and endpoint shape.

## Installation

```bash
cd mermaid/agent-harness
python -m pip install -e .[dev]
```

## Usage

### One-shot commands

```bash
# Create a new Mermaid project
cli-anything-mermaid project new --sample flowchart -o diagram.mermaid.json

# Replace the current diagram source
cli-anything-mermaid --project diagram.mermaid.json diagram set --text "graph TD; A[Test] --> B[Works]"

# Show a shareable URL
cli-anything-mermaid --project diagram.mermaid.json export share --mode view

# Render via the Mermaid renderer backend
cli-anything-mermaid --project diagram.mermaid.json export render output.svg -f svg --overwrite
cli-anything-mermaid --project diagram.mermaid.json export render output.png -f png --overwrite
```

### JSON mode

```bash
cli-anything-mermaid --json project new -o diagram.mermaid.json
cli-anything-mermaid --json --project diagram.mermaid.json export share --mode edit
cli-anything-mermaid --json --project diagram.mermaid.json export render output.svg -f svg --overwrite
```

### Interactive REPL

```bash
cli-anything-mermaid
cli-anything-mermaid --project diagram.mermaid.json
```

## Command reference

- `project new/open/save/info/samples`
- `diagram set/show`
- `export render/share`
- `session status/undo/redo`

## Notes

- Project files are stored as `.mermaid.json`
- Rendered output is produced through the same serialized state payload the live editor uses
- Overwriting a symlink updates its resolved target and keeps the link. Output is staged beside the target; existing restrictive permissions apply while the staged content is written.
- `export share` emits URLs that open in Mermaid Live Editor

## Running tests

```bash
cd mermaid/agent-harness
python -m pytest cli_anything/mermaid/tests/ -v --tb=no
```

### Output file safety

New render outputs follow the process umask. Existing-file overwrites use a
same-directory staging file and preserve ownership, modes and access-control
metadata on macOS and Linux before publication. A metadata-copy error leaves the
original intact and returns a clean error. Directory-shaped paths ending in a
separator are rejected before contacting the renderer.

Existing-file overwrite is refused before renderer access on other platforms,
including Windows, because this backend cannot currently preserve their access
controls. Creating a new output remains supported. Native Windows overwrite
qualification is not claimed.
