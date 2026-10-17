"""Pinned source installs use actual local Git processes and an owned npm fixture."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from cli_hub import installer

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def source_install(monkeypatch, tmp_path):
    # No Git config, remote network, credentials or actual npm installation.
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    repository = tmp_path / "source"
    package = repository / "sketch/agent-harness"
    (package / "src").mkdir(parents=True)
    (package / "src/cli.js").write_text("#!/usr/bin/env node\nconsole.log('fixture')\n")
    (package / "package.json").write_text(json.dumps({
        "name": "sketch-harness", "version": "1.0.0", "bin": {"sketch-cli": "src/cli.js"},
    }))
    def git(*args):
        return subprocess.check_output(["git", "-C", str(repository), *args], text=True).strip()
    git("init", "--quiet")
    git("add", ".")
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "commit", "--quiet", "--no-gpg-sign", "-m", "fixture")
    ref = git("rev-parse", "HEAD")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "2")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "url." + repository.as_uri() + ".insteadOf")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "https://github.com/HKUDS/CLI-Anything.git")
    monkeypatch.setenv("GIT_CONFIG_KEY_1", "protocol.file.allow")
    monkeypatch.setenv("GIT_CONFIG_VALUE_1", "always")
    monkeypatch.setenv("GIT_ALLOW_PROTOCOL", "file")
    binary = tmp_path / "bin"
    binary.mkdir()
    npm = binary / "npm"
    npm.write_text("#!" + sys.executable + "\n" + '''
import io,json,os,sys,tarfile
from pathlib import Path
args=sys.argv[1:]; fault=os.environ.get("CLI_HUB_PACK_FAULT", "")
log=Path(os.environ["CLI_HUB_NPM_LOG"])
with log.open("a") as out: out.write(json.dumps({"args":args,"cwd":os.getcwd()})+"\\n")
if args[0]=="pack":
    if fault=="json": print("not json");sys.exit(0)
    filename="fixture.tgz"
    if fault=="traversal": print(json.dumps([{"filename":"../escape.tgz"}]));sys.exit(0)
    if fault=="missing": print(json.dumps([{"filename":filename}]));sys.exit(0)
    if fault=="archive": Path(filename).write_bytes(b"not a tarball")
    elif fault=="truncated": Path(filename).write_bytes(bytes([31,139,8,0]))
    else:
        manifest=json.loads(Path("package.json").read_text())
        if fault=="name": manifest["name"]="other-package"
        if fault=="bin": manifest["bin"]["sketch-cli"]="../outside.js"
        if fault=="manifest": manifest=[]
        with tarfile.open(filename,"w:gz") as archive:
            content=json.dumps(manifest).encode(); info=tarfile.TarInfo("package/package.json");info.size=len(content);archive.addfile(info,io.BytesIO(content))
            if fault!="missing-bin": archive.add("src/cli.js",arcname="package/src/cli.js")
    print(json.dumps([{"filename":filename}]))
elif args[:2]==["install","--global"]:
    # A tarball install copies a regular entry point out of the transient tree.
    with tarfile.open(args[2],"r:gz") as archive:
        Path(os.environ["CLI_HUB_FIXTURE_GLOBAL"]).write_bytes(archive.extractfile("package/src/cli.js").read())
else: sys.exit(9)
''')
    if os.name == "nt":
        script = npm.with_suffix(".py")
        npm.rename(script)
        npm = npm.with_suffix(".cmd")
        npm.write_text('@echo off\n"' + sys.executable + '" "' + str(script) + '" %*\n')
    else:
        npm.chmod(0o755)
    monkeypatch.setattr(installer, "_find_npm", lambda: str(npm))
    log = tmp_path / "npm.jsonl"
    installed = tmp_path / "installed-cli.js"
    monkeypatch.setenv("CLI_HUB_NPM_LOG", str(log))
    monkeypatch.setenv("CLI_HUB_FIXTURE_GLOBAL", str(installed))
    caller = tmp_path / "unrelated caller"
    caller.mkdir()
    monkeypatch.chdir(caller)
    entry = next(c for c in json.loads((REPO_ROOT / "registry.json").read_text())["clis"] if c["name"] == "sketch")
    entry = dict(entry, source_ref=ref)
    return entry, log, installed, caller


def test_registry_sketch_installs_from_unrelated_cwd(source_install):
    entry, log, installed, caller = source_install
    strategy, (success, message) = installer._perform_action(entry, "install")
    assert success, message
    assert strategy == "npm-source"
    assert installed.read_text().startswith("#!/usr/bin/env node")
    events = [json.loads(line) for line in log.read_text().splitlines()]
    assert events[0]["args"] == ["pack", "--json", "--ignore-scripts"]
    assert events[1]["args"][:2] == ["install", "--global"]
    assert Path(events[0]["cwd"]) != caller
    assert not Path(events[1]["args"][2]).exists(), "temporary checkout must be removed"
    assert not installed.is_symlink(), "installed fixture must survive checkout cleanup"
    assert list(caller.iterdir()) == []


@pytest.mark.parametrize("fault", ["json", "traversal", "missing", "archive", "truncated", "name", "bin", "manifest", "missing-bin"])
def test_bad_pack_output_is_not_installed(source_install, monkeypatch, fault):
    entry, log, installed, _ = source_install
    monkeypatch.setenv("CLI_HUB_PACK_FAULT", fault)
    _, (success, message) = installer._perform_action(entry, "install")
    assert not success
    assert "Source package install failed" in message
    assert not installed.exists()
    assert len(log.read_text().splitlines()) == 1


@pytest.mark.parametrize("field,value", [
    ("source_repo", "file:///tmp/private"), ("source_repo", "https://user:secret@example.com/repo"),
    ("source_ref", "main"), ("source_ref", "-bad"), ("source_subdir", "../escape"),
    ("source_subdir", "/absolute"), ("source_subdir", "a/../escape"),
    ("source_subdir", "a\\escape"), ("source_subdir", "."),
])
def test_source_contract_refuses_unpinned_or_uncontained_values(field, value):
    entry = {"source_repo": "https://example.com/repo.git", "source_ref": "a" * 40,
             "source_subdir": "sketch/agent-harness", field: value}
    with pytest.raises(ValueError):
        installer._npm_source_fields(entry)


@pytest.mark.parametrize("action", ["install", "update", "uninstall"])
def test_malformed_generic_command_is_a_clean_failure(action):
    entry = {"install_strategy": "command", "name": "fixture", "display_name": "Fixture",
             "entry_point": "fixture", action + "_cmd": 'tool "unclosed'}
    _, (success, message) = installer._perform_action(entry, action)
    assert not success
    assert "Invalid command" in message
