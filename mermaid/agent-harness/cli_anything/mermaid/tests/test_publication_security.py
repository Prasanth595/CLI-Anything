"""Filesystem publication controls over real loopback renderer requests."""

import http.server
import os
import stat
import sys
import threading

import pytest

from cli_anything.mermaid.utils import mermaid_backend


SUPPORTED = sys.platform == "darwin" or sys.platform.startswith("linux")
pytestmark = pytest.mark.skipif(not SUPPORTED, reason="native overwrite metadata profile")


@pytest.fixture
def renderer(monkeypatch):
    servers = []

    def start(during_request=lambda: None):
        calls = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                calls.append(self.path)
                during_request()
                data = b"<svg>new render</svg>"
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append((server, thread))
        monkeypatch.setattr(mermaid_backend, "RENDER_BASE", f"http://127.0.0.1:{server.server_port}")
        return calls

    yield start
    for server, thread in servers:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def test_chmod_during_renderer_request_preserves_tightened_mode(renderer, tmp_path):
    target = tmp_path / "out.svg"
    target.write_bytes(b"original")
    target.chmod(0o644)
    calls = renderer(lambda: target.chmod(0o600))
    mermaid_backend.render_to_file("pako:x", str(target), "svg")
    assert calls
    assert target.read_bytes() == b"<svg>new render</svg>"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert not list(tmp_path.glob(".mermaid-*"))


def test_replaced_inode_during_renderer_request_is_not_overwritten(renderer, tmp_path):
    target = tmp_path / "out.svg"
    target.write_bytes(b"original")
    previous = tmp_path / "previous.svg"

    def replace():
        target.rename(previous)
        target.write_bytes(b"another writer")

    calls = renderer(replace)
    with pytest.raises(RuntimeError, match="identity changed"):
        mermaid_backend.render_to_file("pako:x", str(target), "svg")
    assert calls
    assert previous.read_bytes() == b"original"
    assert target.read_bytes() == b"another writer"
    assert not list(tmp_path.glob(".mermaid-*"))


def test_destination_created_during_renderer_request_is_not_overwritten(renderer, tmp_path):
    target = tmp_path / "out.svg"
    renderer(lambda: target.write_bytes(b"another writer"))
    with pytest.raises(RuntimeError, match="identity changed"):
        mermaid_backend.render_to_file("pako:x", str(target), "svg")
    assert target.read_bytes() == b"another writer"
    assert not list(tmp_path.glob(".mermaid-*"))


@pytest.mark.parametrize("when", ["before", "during_fetch"])
def test_hard_linked_output_is_explicitly_refused(renderer, tmp_path, when):
    target = tmp_path / "out.svg"
    alias = tmp_path / "alias.svg"
    target.write_bytes(b"original")
    if when == "before":
        os.link(target, alias)
        calls = renderer()
    else:
        calls = renderer(lambda: os.link(target, alias))
    with pytest.raises(RuntimeError, match="hard.link"):
        mermaid_backend.render_to_file("pako:x", str(target), "svg")
    assert bool(calls) is (when == "during_fetch")
    assert target.read_bytes() == alias.read_bytes() == b"original"
    assert target.stat().st_ino == alias.stat().st_ino
    assert target.stat().st_nlink == 2
    assert not list(tmp_path.glob(".mermaid-*"))


@pytest.mark.parametrize("mode", [0o4755, 0o2750, 0o6750])
def test_new_render_bytes_do_not_inherit_setid_privileges(renderer, tmp_path, mode):
    target = tmp_path / "out.svg"
    target.write_bytes(b"original")
    target.chmod(mode)
    if stat.S_IMODE(target.stat().st_mode) != mode:
        pytest.skip("filesystem does not retain the requested set-ID fixture mode")
    renderer()
    mermaid_backend.render_to_file("pako:x", str(target), "svg")
    assert target.read_bytes() == b"<svg>new render</svg>"
    assert stat.S_IMODE(target.stat().st_mode) == mode & ~(stat.S_ISUID | stat.S_ISGID)


@pytest.mark.parametrize("change", ["chmod", "replace", "hard_link"])
def test_change_while_staging_is_detected_before_publication(monkeypatch, renderer, tmp_path, change):
    target = tmp_path / "out.svg"
    target.write_bytes(b"original")
    target.chmod(0o644)
    renderer()
    real_fdopen = os.fdopen

    class Writer:
        def __init__(self, file):
            self.file = file
        def __enter__(self):
            self.file.__enter__()
            return self
        def __exit__(self, *args):
            return self.file.__exit__(*args)
        def write(self, data):
            written = self.file.write(data)
            if change == "chmod":
                target.chmod(0o600)
            elif change == "replace":
                target.rename(tmp_path / "previous.svg")
                target.write_bytes(b"another writer")
            else:
                os.link(target, tmp_path / "alias.svg")
            return written

    monkeypatch.setattr(os, "fdopen", lambda fd, *args, **kwargs: Writer(real_fdopen(fd, *args, **kwargs)))
    if change == "chmod":
        mermaid_backend.render_to_file("pako:x", str(target), "svg")
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
        assert target.read_bytes() == b"<svg>new render</svg>"
    else:
        with pytest.raises(RuntimeError, match="identity changed|hard.link"):
            mermaid_backend.render_to_file("pako:x", str(target), "svg")
        assert target.read_bytes() == (b"another writer" if change == "replace" else b"original")
    assert not list(tmp_path.glob(".mermaid-*"))


def test_linux_attribute_copy_excludes_file_capabilities_but_keeps_labels(monkeypatch, tmp_path):
    """Linux xattr branch emulation; not a native Linux capability qualification."""
    target = tmp_path / "out.svg"
    staged = tmp_path / "stage.svg"
    target.write_bytes(b"original")
    staged.write_bytes(b"new render")
    attributes = {
        str(target): {"security.capability": b"source-privilege", "security.selinux": b"label", "system.posix_acl_access": b"acl"},
        str(staged): {"security.capability": b"inherited-privilege"},
    }
    monkeypatch.setattr(mermaid_backend.sys, "platform", "linux")
    monkeypatch.setattr(os, "listxattr", lambda path: list(attributes[str(path)]), raising=False)
    monkeypatch.setattr(os, "getxattr", lambda path, name: attributes[str(path)][name], raising=False)
    monkeypatch.setattr(os, "setxattr", lambda path, name, value: attributes[str(path)].__setitem__(name, value), raising=False)
    monkeypatch.setattr(os, "removexattr", lambda path, name: attributes[str(path)].pop(name), raising=False)
    mermaid_backend._preserve_destination_metadata(str(target), str(staged), target.stat())
    assert attributes[str(staged)] == {"security.selinux": b"label", "system.posix_acl_access": b"acl"}
    assert attributes[str(target)]["security.capability"] == b"source-privilege"


def test_access_control_change_during_final_metadata_copy_aborts(monkeypatch, renderer, tmp_path):
    target = tmp_path / "out.svg"
    target.write_bytes(b"original")
    target.chmod(0o644)
    renderer()
    preserve = mermaid_backend._preserve_destination_metadata
    calls = []

    def change_after_copy(*args):
        preserve(*args)
        calls.append(True)
        target.chmod(0o600)

    monkeypatch.setattr(mermaid_backend, "_preserve_destination_metadata", change_after_copy)
    with pytest.raises(RuntimeError, match="metadata changed"):
        mermaid_backend.render_to_file("pako:x", str(target), "svg")
    assert target.read_bytes() == b"original"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert not list(tmp_path.glob(".mermaid-*"))


def test_deleted_destination_during_renderer_request_is_not_recreated(renderer, tmp_path):
    target = tmp_path / "out.svg"
    target.write_bytes(b"original")
    renderer(target.unlink)
    with pytest.raises(RuntimeError, match="identity changed"):
        mermaid_backend.render_to_file("pako:x", str(target), "svg")
    assert not target.exists()
    assert not list(tmp_path.glob(".mermaid-*"))


def test_same_inode_reached_through_new_symlink_is_not_published_over(renderer, tmp_path):
    target = tmp_path / "out.svg"
    previous = tmp_path / "previous.svg"
    target.write_bytes(b"original")

    def change_entry():
        target.rename(previous)
        target.symlink_to(previous.name)

    renderer(change_entry)
    with pytest.raises(RuntimeError, match="not a regular file|identity changed"):
        mermaid_backend.render_to_file("pako:x", str(target), "svg")
    assert target.is_symlink()
    assert target.read_bytes() == previous.read_bytes() == b"original"
    assert not list(tmp_path.glob(".mermaid-*"))


@pytest.mark.skipif(sys.platform != "darwin", reason="native macOS inherited ACL fixture")
def test_overwrite_stage_is_private_until_all_bytes_are_written(monkeypatch, renderer, tmp_path):
    import subprocess
    parent = tmp_path / "outputs"
    parent.mkdir()
    subprocess.run(["/bin/chmod", "+a", "everyone allow read,file_inherit", str(parent)], check=True)
    target = parent / "out.svg"
    target.write_bytes(b"original")
    target.chmod(0o644)
    original_acl = subprocess.check_output(["/bin/ls", "-le", str(target)], text=True).splitlines()[1:]
    assert original_acl, "fixture must inherit a real extended ACL"
    renderer()
    real_fdopen = os.fdopen
    observations = []

    class Writer:
        def __init__(self, file):
            self.file = file
        def __enter__(self):
            self.file.__enter__()
            return self
        def __exit__(self, *args):
            return self.file.__exit__(*args)
        def write(self, data):
            written = self.file.write(data)
            self.file.flush()
            staged = next(parent.glob(".mermaid-*"))
            observations.append((stat.S_IMODE(staged.stat().st_mode),
                subprocess.check_output(["/bin/ls", "-le", str(staged)], text=True).splitlines()[1:]))
            return written

    monkeypatch.setattr(os, "fdopen", lambda fd, *args, **kwargs: Writer(real_fdopen(fd, *args, **kwargs)))
    mermaid_backend.render_to_file("pako:x", str(target), "svg")
    assert observations == [(0o600, [])]
    assert subprocess.check_output(["/bin/ls", "-le", str(target)], text=True).splitlines()[1:] == original_acl
    assert target.read_bytes() == b"<svg>new render</svg>"


@pytest.mark.skipif(sys.platform != "darwin", reason="native macOS ACL ordering")
def test_target_acl_is_installed_before_stage_mode_is_widened(monkeypatch, renderer, tmp_path):
    import subprocess
    target = tmp_path / "out.svg"
    target.write_bytes(b"original")
    target.chmod(0o644)
    subprocess.run(["/bin/chmod", "+a", "user:nobody deny read", str(target)], check=True)
    expected_acl = subprocess.check_output(["/bin/ls", "-le", str(target)], text=True).splitlines()[1:]
    assert expected_acl
    renderer()
    chmod = os.chmod
    widened = []

    def check_before_widening(path, mode):
        if mode == 0o644 and ".mermaid-" in str(path):
            observed = subprocess.check_output(["/bin/ls", "-le", str(path)], text=True).splitlines()[1:]
            assert observed == expected_acl
            widened.append(True)
        return chmod(path, mode)

    monkeypatch.setattr(os, "chmod", check_before_widening)
    mermaid_backend.render_to_file("pako:x", str(target), "svg")
    assert widened == [True]
    assert stat.S_IMODE(target.stat().st_mode) == 0o644
    assert subprocess.check_output(["/bin/ls", "-le", str(target)], text=True).splitlines()[1:] == expected_acl
