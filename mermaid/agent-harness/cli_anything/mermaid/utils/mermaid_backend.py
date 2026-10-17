"""Backend helpers for Mermaid Live Editor render/share state."""

from __future__ import annotations

import base64
import json
import os
import secrets
import stat
import urllib.error
import urllib.request
import zlib


RENDER_BASE = os.environ.get("MERMAID_RENDERER_URL", "https://mermaid.ink").rstrip("/")
LIVE_BASE = os.environ.get("MERMAID_LIVE_URL", "https://mermaid.live").rstrip("/")


def serialize_state(state: dict) -> str:
    payload = json.dumps(state, separators=(",", ":")).encode("utf-8")
    compressed = zlib.compress(payload, level=9)
    encoded = base64.urlsafe_b64encode(compressed).decode("ascii").rstrip("=")
    return f"pako:{encoded}"


def build_render_url(serialized: str, fmt: str) -> str:
    if fmt == "svg":
        return f"{RENDER_BASE}/svg/{serialized}"
    if fmt == "png":
        return f"{RENDER_BASE}/img/{serialized}?type=png"
    raise ValueError(f"Unsupported format: {fmt}")


def build_live_url(serialized: str, mode: str) -> str:
    if mode not in {"edit", "view"}:
        raise ValueError(f"Unsupported share mode: {mode}")
    return f"{LIVE_BASE}/{mode}#{serialized}"


def render_to_file(serialized: str, output_path: str, fmt: str) -> dict:
    url = build_render_url(serialized, fmt)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            data = response.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"Renderer returned HTTP {exc.code} for {fmt} output "
            "(check the diagram syntax)"
        ) from exc
    except (urllib.error.URLError, OSError) as exc:
        raise RuntimeError(f"Could not reach renderer {RENDER_BASE}: {exc}") from exc
    temporary_path = None
    pending_fd = None
    try:
        parent = os.path.dirname(os.path.abspath(output_path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        try:
            destination_mode = stat.S_IMODE(os.stat(output_path).st_mode)
        except FileNotFoundError:
            destination_mode = None
        candidate_path = os.path.join(parent, f".mermaid-{secrets.token_hex(16)}")
        pending_fd = os.open(candidate_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
        temporary_path = candidate_path  # Cleanup only a file we created successfully.
        output_file = os.fdopen(pending_fd, "wb")
        pending_fd = None  # The file object now owns the descriptor.
        with output_file as fh:
            if fh.write(data) != len(data):
                raise OSError("Incomplete renderer output write")
        file_size = os.path.getsize(temporary_path)
        if file_size != len(data):
            raise OSError("Incomplete renderer output file")
        if destination_mode is not None:
            os.chmod(temporary_path, destination_mode)
        os.replace(temporary_path, output_path)
    except OSError as exc:
        raise RuntimeError(f"Could not write {output_path}: {exc}") from exc
    finally:
        # Best-effort cleanup must not hide the original write error.
        if pending_fd is not None:
            try:
                os.close(pending_fd)
            except OSError:
                pass
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass
    return {
        "output": os.path.abspath(output_path),
        "format": fmt,
        "method": "mermaid-renderer",
        "file_size": file_size,
        "url": url,
    }
