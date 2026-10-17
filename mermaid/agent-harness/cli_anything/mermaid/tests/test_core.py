import json
import os
import tempfile

import pytest

from cli_anything.mermaid.core import export as export_mod
from cli_anything.mermaid.core import project as project_mod
from cli_anything.mermaid.core.session import Session, default_state
from cli_anything.mermaid.utils import mermaid_backend


def test_default_state():
    state = default_state()
    assert "flowchart TD" in state["code"]
    assert json.loads(state["mermaid"])["theme"] == "default"


def test_save_open_roundtrip():
    session = Session()
    project_mod.new_project(session, sample="sequence")
    with tempfile.NamedTemporaryFile(suffix=".mermaid.json", delete=False) as fh:
        path = fh.name
    try:
        project_mod.save_project(session, path)
        other = Session()
        result = project_mod.open_project(other, path)
        assert result["line_count"] > 0
        assert "sequenceDiagram" in other.state["code"]
    finally:
        os.unlink(path)


def test_undo_redo():
    session = Session()
    session.new_project()
    original = session.state["code"]
    session.set_code("graph TD\n  A --> B\n")
    assert session.undo() is True
    assert session.state["code"] == original
    assert session.redo() is True
    assert "A --> B" in session.state["code"]


def test_backend_serialization_and_urls():
    serialized = mermaid_backend.serialize_state(default_state())
    assert serialized.startswith("pako:")
    assert mermaid_backend.build_render_url(serialized, "svg").startswith("https://")
    assert mermaid_backend.build_live_url(serialized, "view").startswith("https://")


def test_share_payload():
    session = Session()
    session.new_project()
    result = export_mod.share(session, mode="edit")
    assert result["mode"] == "edit"
    assert result["url"].startswith("https://mermaid.live/edit#")


def test_open_project_rejects_invalid_files():
    import pytest
    with tempfile.TemporaryDirectory() as tmp:
        bad_json = os.path.join(tmp, "bad.json")
        with open(bad_json, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        no_code = os.path.join(tmp, "nocode.json")
        with open(no_code, "w", encoding="utf-8") as fh:
            json.dump({"foo": 1}, fh)
        for path in (bad_json, no_code):
            session = Session()
            with pytest.raises(ValueError):
                session.open_project(path)
            assert not session.is_open


def test_render_http_error_is_runtime_error(monkeypatch):
    import io
    import urllib.error
    import pytest

    def boom(req, timeout=0):
        raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {}, io.BytesIO(b""))

    monkeypatch.setattr(mermaid_backend.urllib.request, "urlopen", boom)
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "out.svg")
        with pytest.raises(RuntimeError, match="HTTP 400"):
            mermaid_backend.render_to_file("pako:x", out, "svg")
        assert not os.path.exists(out)


def test_cli_reports_errors_without_traceback():
    from click.testing import CliRunner
    from cli_anything.mermaid.mermaid_cli import cli

    with tempfile.TemporaryDirectory() as tmp:
        bad = os.path.join(tmp, "bad.json")
        with open(bad, "w", encoding="utf-8") as fh:
            fh.write("[]")
        result = CliRunner().invoke(cli, ["project", "open", bad])
        assert result.exit_code == 1
        assert "Error: Invalid Mermaid project file" in result.output
        assert not isinstance(result.exception, ValueError)


def test_nested_help_exits_zero():
    from click.testing import CliRunner
    from cli_anything.mermaid.mermaid_cli import cli

    for args in (["project", "--help"], ["diagram", "set", "--help"]):
        result = CliRunner().invoke(cli, args)
        assert result.exit_code == 0, result.output
        assert "Error" not in result.output


def test_autosave_failure_is_clean_error(monkeypatch):
    from click.testing import CliRunner
    from cli_anything.mermaid.core.session import Session
    from cli_anything.mermaid.mermaid_cli import cli

    def boom(self, *a, **k):
        raise PermissionError("read-only")

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "p.mermaid.json")
        session = Session()
        project_mod.new_project(session)
        project_mod.save_project(session, path)
        monkeypatch.setattr(Session, "save_project", boom)
        result = CliRunner().invoke(cli, ["--project", path, "diagram", "set", "--text", "graph TD; A-->B"])
        assert result.exit_code == 1
        assert "Error: read-only" in result.output
        assert not isinstance(result.exception, PermissionError)


def test_render_write_failure_is_runtime_error(monkeypatch, tmp_path):
    import io

    import pytest

    class FakeResp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(mermaid_backend.urllib.request, "urlopen", lambda *a, **k: FakeResp(b"<svg/>"))
    blocker = tmp_path / "file"
    blocker.write_text("x")
    with pytest.raises(RuntimeError, match="Could not write"):
        mermaid_backend.render_to_file("pako:x", str(blocker / "out.svg"), "svg")


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("failure_mode", ["error", "short_write"])
def test_render_partial_write_preserves_destination(monkeypatch, tmp_path, existing, failure_mode):
    import io
    import pytest

    monkeypatch.setattr(mermaid_backend.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(b"<svg>complete</svg>"))
    target = tmp_path / "out.svg"
    if existing:
        target.write_bytes(b"original")
    real_open = open
    real_fdopen = os.fdopen

    class PartialWriter:
        def __init__(self, file):
            self.file = file
        def __enter__(self):
            self.file.__enter__()
            return self
        def __exit__(self, *args):
            return self.file.__exit__(*args)
        def write(self, data):
            self.file.write(data[:3])
            if failure_mode == "error":
                raise OSError("simulated disk full after partial write")
            return 3

    def partial_open(path, mode="r", *args, **kwargs):
        file = real_open(path, mode, *args, **kwargs)
        return PartialWriter(file) if mode == "wb" else file

    def partial_fdopen(fd, *args, **kwargs):
        return PartialWriter(real_fdopen(fd, *args, **kwargs))

    monkeypatch.setattr("builtins.open", partial_open)
    monkeypatch.setattr(os, "fdopen", partial_fdopen)
    with pytest.raises(RuntimeError, match="Could not write"):
        mermaid_backend.render_to_file("pako:x", str(target), "svg")
    if existing:
        assert target.read_bytes() == b"original"
    else:
        assert not target.exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == (["out.svg"] if existing else [])


def test_render_success_publishes_complete_output(monkeypatch, tmp_path):
    import io
    data = b"<svg>complete</svg>"
    monkeypatch.setattr(mermaid_backend.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(data))
    target = tmp_path / "out.svg"
    target.write_bytes(b"original")
    result = mermaid_backend.render_to_file("pako:x", str(target), "svg")
    assert target.read_bytes() == data
    assert result["file_size"] == len(data)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["out.svg"]


@pytest.mark.parametrize("failure_mode", ["size", "replace", "chmod"])
def test_render_publish_failure_preserves_destination(monkeypatch, tmp_path, failure_mode):
    import io
    data = b"<svg>complete</svg>"
    monkeypatch.setattr(mermaid_backend.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(data))
    target = tmp_path / "out.svg"
    target.write_bytes(b"original")
    if failure_mode == "size":
        monkeypatch.setattr(os.path, "getsize", lambda path: 0)
    elif failure_mode == "chmod":
        def fail_chmod(path, mode):
            raise OSError("simulated chmod failure")
        monkeypatch.setattr(os, "chmod", fail_chmod)
    else:
        def fail_replace(source, destination):
            raise OSError("simulated replace failure")
        monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(RuntimeError, match="Could not write"):
        mermaid_backend.render_to_file("pako:x", str(target), "svg")
    assert target.read_bytes() == b"original"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["out.svg"]


def test_render_cleanup_failure_preserves_write_diagnostic(monkeypatch, tmp_path):
    import io
    data = b"<svg>complete</svg>"
    target = tmp_path / "out.svg"
    target.write_bytes(b"original")
    with monkeypatch.context() as patch:
        patch.setattr(mermaid_backend.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(data))
        def fail_size(path):
            raise OSError("original size-check failure")
        def fail_unlink(path):
            raise OSError("cleanup denied")
        patch.setattr(os.path, "getsize", fail_size)
        patch.setattr(os, "unlink", fail_unlink)
        with pytest.raises(RuntimeError, match="original size-check failure") as error:
            mermaid_backend.render_to_file("pako:x", str(target), "svg")
        assert str(error.value.__cause__) == "original size-check failure"
        assert target.read_bytes() == b"original"
        assert len(list(tmp_path.glob(".mermaid-*"))) == 1
    # Cleanup denial was injected; remove the deliberately retained temporary file.
    for temporary in tmp_path.glob(".mermaid-*"):
        temporary.unlink()


def test_render_fdopen_failure_closes_descriptor(monkeypatch, tmp_path):
    import io
    descriptors = []
    monkeypatch.setattr(mermaid_backend.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(b"<svg/>"))
    def fail_fdopen(fd, *args, **kwargs):
        descriptors.append(fd)
        raise OSError("fdopen failed")
    monkeypatch.setattr(os, "fdopen", fail_fdopen)
    with pytest.raises(RuntimeError, match="fdopen failed"):
        mermaid_backend.render_to_file("pako:x", str(tmp_path / "out.svg"), "svg")
    assert len(descriptors) == 1
    try:
        with pytest.raises(OSError):
            os.fstat(descriptors[0])
    finally:
        # The baseline leaks; keep the failing control from leaking into later tests.
        try:
            os.close(descriptors[0])
        except OSError:
            pass
    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode contract")
@pytest.mark.parametrize("mask,expected", [(0o022, 0o644), (0o077, 0o600)])
def test_new_render_respects_creation_umask(monkeypatch, tmp_path, mask, expected):
    import io
    import stat
    monkeypatch.setattr(mermaid_backend.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(b"<svg/>"))
    target = tmp_path / "out.svg"
    previous = os.umask(mask)
    try:
        mermaid_backend.render_to_file("pako:x", str(target), "svg")
    finally:
        os.umask(previous)
    assert stat.S_IMODE(target.stat().st_mode) == expected


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode contract")
@pytest.mark.parametrize("mode", [0o644, 0o640, 0o444])
def test_render_preserves_existing_destination_mode(monkeypatch, tmp_path, mode):
    import io
    import stat
    monkeypatch.setattr(mermaid_backend.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(b"<svg/>"))
    target = tmp_path / "out.svg"
    target.write_bytes(b"original")
    target.chmod(mode)
    previous = os.umask(0o077)
    try:
        mermaid_backend.render_to_file("pako:x", str(target), "svg")
    finally:
        os.umask(previous)
    assert stat.S_IMODE(target.stat().st_mode) == mode
    assert target.read_bytes() == b"<svg/>"


@pytest.mark.parametrize("number,clean_error", [(32, False), (28, True)])
def test_cli_leaves_broken_pipe_handling_to_click(monkeypatch, number, clean_error):
    import errno
    from click.testing import CliRunner
    from cli_anything.mermaid import mermaid_cli

    def broken_output(*args, **kwargs):
        raise OSError(number, "output failed")
    monkeypatch.setattr(mermaid_cli.click, "echo", broken_output)
    result = CliRunner().invoke(mermaid_cli.cli, ["project", "samples"])
    assert result.exit_code == 1
    assert ("Error:" in result.output) == clean_error
    if not clean_error:
        assert "output failed" not in result.output



def test_render_exclusive_creation_never_removes_a_collision(monkeypatch, tmp_path):
    import io
    monkeypatch.setattr(mermaid_backend.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(b"<svg/>"))
    monkeypatch.setattr(mermaid_backend.secrets, "token_hex", lambda count: "collision")
    collision = tmp_path / ".mermaid-collision"
    collision.write_bytes(b"another writer")
    target = tmp_path / "out.svg"
    target.write_bytes(b"original")
    with pytest.raises(RuntimeError, match="Could not write"):
        mermaid_backend.render_to_file("pako:x", str(target), "svg")
    assert collision.read_bytes() == b"another writer"
    assert target.read_bytes() == b"original"


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode contract")
@pytest.mark.parametrize("mode", [0o600, 0o640, 0o400])
def test_render_temporary_is_restrictive_while_content_is_written(monkeypatch, tmp_path, mode):
    import io
    import stat
    monkeypatch.setattr(mermaid_backend.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(b"private render"))
    target = tmp_path / "out.svg"
    target.write_bytes(b"original")
    target.chmod(mode)
    real_fdopen = os.fdopen
    observed = []

    class ObservedWriter:
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
            observed.append(stat.S_IMODE(os.fstat(self.file.fileno()).st_mode))
            return written

    monkeypatch.setattr(os, "fdopen", lambda fd, *a, **k: ObservedWriter(real_fdopen(fd, *a, **k)))
    previous = os.umask(0o022)
    try:
        mermaid_backend.render_to_file("pako:x", str(target), "svg")
    finally:
        os.umask(previous)
    assert observed == [mode]
    assert stat.S_IMODE(target.stat().st_mode) == mode
    assert target.read_bytes() == b"private render"


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode contract")
def test_render_failed_cleanup_keeps_restrictive_temporary_mode(monkeypatch, tmp_path):
    import io
    import stat
    target = tmp_path / "out.svg"
    target.write_bytes(b"original")
    target.chmod(0o600)
    with monkeypatch.context() as patch:
        patch.setattr(mermaid_backend.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(b"private render"))
        def fail_size(path):
            raise OSError("original size failure")
        def fail_cleanup(path):
            raise OSError("cleanup denied")
        patch.setattr(os.path, "getsize", fail_size)
        patch.setattr(os, "unlink", fail_cleanup)
        previous = os.umask(0o022)
        try:
            with pytest.raises(RuntimeError, match="original size failure"):
                mermaid_backend.render_to_file("pako:x", str(target), "svg")
        finally:
            os.umask(previous)
        pending = list(tmp_path.glob(".mermaid-*"))
        assert len(pending) == 1
        assert stat.S_IMODE(pending[0].stat().st_mode) == 0o600
        assert target.read_bytes() == b"original"
    for pending_file in tmp_path.glob(".mermaid-*"):
        pending_file.unlink()


@pytest.mark.parametrize("existing_target", [False, True])
def test_render_follows_output_symlink_and_publishes_beside_target(monkeypatch, tmp_path, existing_target):
    import io
    target_dir = tmp_path / "releases"
    target_dir.mkdir()
    target = target_dir / "v1.svg"
    if existing_target:
        target.write_bytes(b"original")
    output = tmp_path / "current.svg"
    output.symlink_to("releases/v1.svg")
    monkeypatch.setattr(mermaid_backend.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(b"new render"))
    real_replace = os.replace
    published = []
    def record_replace(source, destination):
        published.append((os.path.dirname(source), os.path.abspath(destination)))
        return real_replace(source, destination)
    monkeypatch.setattr(os, "replace", record_replace)
    session = Session()
    session.new_project()
    result = export_mod.render(session, str(output), fmt="svg", overwrite=True)
    assert output.is_symlink()
    assert os.readlink(output) == "releases/v1.svg"
    assert target.read_bytes() == b"new render"
    assert output.read_bytes() == b"new render"
    assert published == [(str(target_dir), str(target))]
    assert result["output"] == str(output)
    assert not list(tmp_path.rglob(".mermaid-*"))


@pytest.mark.parametrize("failure_mode", ["size", "replace"])
def test_render_failure_preserves_output_symlink_and_target(monkeypatch, tmp_path, failure_mode):
    import io
    target_dir = tmp_path / "releases"
    target_dir.mkdir()
    target = target_dir / "v1.svg"
    target.write_bytes(b"original")
    output = tmp_path / "current.svg"
    output.symlink_to("releases/v1.svg")
    monkeypatch.setattr(mermaid_backend.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(b"new render"))
    if failure_mode == "size":
        monkeypatch.setattr(os.path, "getsize", lambda path: 0)
    else:
        def fail_replace(source, destination):
            raise OSError("replace denied")
        monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(RuntimeError, match="Could not write"):
        mermaid_backend.render_to_file("pako:x", str(output), "svg")
    assert output.is_symlink()
    assert target.read_bytes() == b"original"
    assert not list(tmp_path.rglob(".mermaid-*"))


def test_cli_overwrite_updates_symlink_target(monkeypatch, tmp_path):
    import io
    from click.testing import CliRunner
    from cli_anything.mermaid import mermaid_cli
    target = tmp_path / "v1.svg"
    target.write_bytes(b"original")
    output = tmp_path / "current.svg"
    output.symlink_to("v1.svg")
    project = tmp_path / "diagram.mermaid.json"
    runner = CliRunner()
    created = runner.invoke(mermaid_cli.cli, ["project", "new", "-o", str(project)])
    assert created.exit_code == 0, created.output
    monkeypatch.setattr(mermaid_backend.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(b"new render"))
    result = runner.invoke(mermaid_cli.cli, ["--project", str(project), "--json", "export", "render", str(output), "--overwrite"])
    assert result.exit_code == 0, result.output
    assert output.is_symlink()
    assert target.read_bytes() == b"new render"
