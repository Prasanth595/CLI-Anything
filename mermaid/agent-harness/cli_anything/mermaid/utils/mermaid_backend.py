"""Backend helpers for Mermaid Live Editor render/share state."""

from __future__ import annotations

import base64
import json
import os
import secrets
import stat
import sys
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



def _preserve_destination_metadata(source: str, staged: str, original: os.stat_result) -> None:
    """Keep POSIX access controls when atomically replacing an existing file."""
    current = os.stat(staged)
    if (current.st_uid, current.st_gid) != (original.st_uid, original.st_gid):
        os.chown(staged, original.st_uid, original.st_gid)
    os.chmod(staged, stat.S_IMODE(original.st_mode))
    if sys.platform == "darwin":
        # copyfile.h: COPYFILE_ACL (1<<0) | COPYFILE_XATTR (1<<2).
        # Python's os xattr APIs are Linux-only; use macOS's metadata copier.
        import ctypes
        native = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
        copyfile = native.copyfile
        copyfile.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_uint32]
        copyfile.restype = ctypes.c_int
        if copyfile(os.fsencode(source), os.fsencode(staged), None, 5) != 0:
            number = ctypes.get_errno()
            raise OSError(number, os.strerror(number))
    elif sys.platform.startswith("linux"):
        # Linux stores POSIX ACLs and security labels in extended attributes.
        # Unlike shutil.copystat, do not silently ignore access-control errors.
        attributes = {name: os.getxattr(source, name) for name in os.listxattr(source)}
        for name in os.listxattr(staged):
            if name not in attributes:
                os.removexattr(staged, name)
        for name, value in attributes.items():
            os.setxattr(staged, name, value)
        if {name: os.getxattr(staged, name) for name in os.listxattr(staged)} != attributes:
            raise OSError("Could not preserve destination extended attributes")
    else:
        raise OSError("Preserving overwrite access controls is supported only on macOS and Linux")
    after = os.stat(staged)
    if (after.st_uid, after.st_gid, stat.S_IMODE(after.st_mode)) != (
        original.st_uid, original.st_gid, stat.S_IMODE(original.st_mode)
    ):
        raise OSError("Could not preserve destination ownership and mode")


def render_to_file(serialized: str, output_path: str, fmt: str) -> dict:
    output_path = os.fspath(output_path)
    separators = tuple(separator for separator in (os.sep, os.altsep) if separator)
    if output_path.endswith(separators):
        raise RuntimeError(f"Could not write {output_path}: output path denotes a directory")
    try:
        destination_path = os.path.realpath(output_path)
        try:
            original = os.stat(destination_path)
        except FileNotFoundError:
            original = None
        if original is not None and not (sys.platform == "darwin" or sys.platform.startswith("linux")):
            raise OSError("Preserving overwrite access controls is supported only on macOS and Linux")
    except OSError as exc:
        raise RuntimeError(f"Could not write {output_path}: {exc}") from exc
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
        parent = os.path.dirname(destination_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        destination_mode = stat.S_IMODE(original.st_mode) if original is not None else None
        candidate_path = os.path.join(parent, f".mermaid-{secrets.token_hex(16)}")
        creation_mode = destination_mode if destination_mode is not None else 0o666
        pending_fd = os.open(candidate_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, creation_mode)
        temporary_path = candidate_path  # Cleanup only a file we created successfully.
        if original is not None:
            _preserve_destination_metadata(destination_path, temporary_path, original)
        output_file = os.fdopen(pending_fd, "wb")
        pending_fd = None  # The file object now owns the descriptor.
        with output_file as fh:
            if fh.write(data) != len(data):
                raise OSError("Incomplete renderer output write")
        file_size = os.path.getsize(temporary_path)
        if file_size != len(data):
            raise OSError("Incomplete renderer output file")
        if original is not None:
            _preserve_destination_metadata(destination_path, temporary_path, original)
        os.replace(temporary_path, destination_path)
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
