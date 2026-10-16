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


@pytest.mark.parametrize("failure_mode", ["size", "replace"])
def test_render_publish_failure_preserves_destination(monkeypatch, tmp_path, failure_mode):
    import io
    data = b"<svg>complete</svg>"
    monkeypatch.setattr(mermaid_backend.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(data))
    target = tmp_path / "out.svg"
    target.write_bytes(b"original")
    if failure_mode == "size":
        monkeypatch.setattr(os.path, "getsize", lambda path: 0)
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
