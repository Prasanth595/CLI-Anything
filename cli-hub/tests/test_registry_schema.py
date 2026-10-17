"""Schema checks for the repo-level registry files (see CONTRIBUTING 'Registry fields')."""

import json
import shlex
from urllib.parse import urlsplit
from pathlib import Path

import pytest

from cli_hub.installer import _install_strategy

REPO_ROOT = Path(__file__).resolve().parents[2]
REQUIRED = {
    "name", "display_name", "version", "description", "requires", "homepage",
    "source_url", "install_cmd", "entry_point", "skill_md", "category",
    "contributors",
}


# Existing noncanonical entries are pinned individually; new local paths and
# qgis must use the documented root skills/ layout without migrating six CLIs.
LEGACY_SKILL_PATHS = {
    "calibre": "calibre/agent-harness/cli_anything/calibre/skills/SKILL.md",
    "lldb": "lldb/agent-harness/cli_anything/lldb/skills/SKILL.md",
    "unrealinsights": "unrealinsights/agent-harness/cli_anything/unrealinsights/skills/SKILL.md",
    "macrocli": "macrocli/agent-harness/cli_anything/macrocli/skills/SKILL.md",
    "3mf": "3MF/agent-harness/cli_anything/threemf/skills/SKILL.md",
    "minimax": "minimax/agent-harness/cli_anything/minimax/skills/SKILL.md",
}


# Registry identifiers whose canonical root skill uses a different spelling.
SKILL_NAME_ALIASES = {
    "cc-switch": "ccswitch",
    "slay_the_spire_ii": "slay-the-spire-ii",
    "unimol_tools": "unimol-tools",
}


def _load(name):
    return json.loads((REPO_ROOT / name).read_text(encoding="utf-8"))["clis"]


def test_names_unique_across_registries():
    # cli_hub.registry merges both files and matches names case-insensitively.
    names = [c["name"].lower() for f in ("registry.json", "public_registry.json") for c in _load(f)]
    assert len(names) == len(set(names))


@pytest.mark.parametrize("fname", ["registry.json", "public_registry.json"])
def test_required_fields_and_contributors_shape(fname):
    for c in _load(fname):
        source = "public" if fname == "public_registry.json" else "harness"
        strategy = _install_strategy(dict(c, _source=source))
        optional = {"install_cmd"} if source == "public" and strategy in {"npm", "bundled"} else set()
        missing = REQUIRED - c.keys() - optional
        assert not missing, f"{c.get('name')} missing {sorted(missing)}"
        # Search, category listing, display, execution, and installed-state
        # consumers rely on actual text, not merely the presence of these keys.
        for field in ("name", "display_name", "version", "description", "homepage", "entry_point", "category"):
            assert isinstance(c[field], str) and c[field].strip(), (
                f"{c.get('name')}: {field} must be nonblank text"
            )
        # These fields explicitly permit null in the contributor contract.
        for field in ("requires", "source_url", "skill_md"):
            assert c[field] is None or isinstance(c[field], str), (
                f"{c['name']}: {field} must be text or null"
            )
        if "install_cmd" not in optional:
            assert isinstance(c["install_cmd"], str) and c["install_cmd"].strip(), (
                f"{c['name']} requires an install command for {strategy}"
            )
        if strategy == "npm":
            assert isinstance(c.get("npm_package"), str) and c["npm_package"].strip(), (
                f"{c['name']} requires npm_package for npm"
            )
        assert "contributor" not in c and "contributor_url" not in c, (
            f"{c['name']} uses legacy contributor fields; use 'contributors' list"
        )
        assert isinstance(c["contributors"], list) and c["contributors"], (
            f"{c['name']} needs at least one contributor"
        )
        for ct in c["contributors"]:
            assert isinstance(ct, dict), f"{c['name']}: contributor must be an object"
            for field in ("name", "url"):
                assert isinstance(ct.get(field), str) and ct[field].strip(), (
                    f"{c['name']}: contributor {field} must be nonblank text"
                )


@pytest.mark.parametrize("fname", ["registry.json", "public_registry.json"])
def test_in_repo_skill_md_paths_exist(fname):
    for c in _load(fname):
        skill = c.get("skill_md")
        # URLs and installer commands (e.g. "npx skills add ...") are not local paths.
        if not skill:
            continue
        source = urlsplit(c.get("source_url") or "")
        source_parts = [part.lower() for part in source.path.strip("/").split("/")[:2]]
        if len(source_parts) == 2:
            source_parts[1] = source_parts[1].removesuffix(".git")
        source_repo = tuple(source_parts)
        standalone = source.scheme in {"http", "https"} and bool(source.netloc) and not (
            (source.hostname or "").lower() in {"github.com", "www.github.com"}
            and source_repo == ("hkuds", "cli-anything")
        )
        external = fname == "public_registry.json" or standalone
        url = urlsplit(skill)
        if url.scheme in {"http", "https"} and url.netloc:
            assert external, f"{c['name']}: in-repo skills must use a local skills/ path"
            continue
        command = shlex.split(skill)
        if len(command) >= 4 and command[:3] == ["npx", "skills", "add"] and not command[3].startswith("-"):
            assert external, f"{c['name']}: in-repo skills must use a local skills/ path"
            continue
        assert not Path(skill).is_absolute() and ".." not in Path(skill).parts, (
            f"{c['name']}: {skill} must be a relative in-repo path"
        )
        assert Path(skill).parts[0] == "skills" or LEGACY_SKILL_PATHS.get(c["name"]) == skill, (
            f"{c['name']}: {skill} must live under skills/"
        )
        skill_name = SKILL_NAME_ALIASES.get(c["name"], c["name"])
        expected = LEGACY_SKILL_PATHS.get(c["name"], f"skills/cli-anything-{skill_name}/SKILL.md")
        assert skill == expected, f"{c['name']}: {skill} must be its own canonical skill {expected}"
        assert (REPO_ROOT / skill).is_file(), f"{c['name']}: {skill} not found"


def test_qgis_skill_md_is_canonical():
    # Regression: qgis once pointed at a harness-internal copy instead of root skills/.
    qgis = next(c for c in _load("registry.json") if c["name"] == "qgis")
    assert qgis["skill_md"] == "skills/cli-anything-qgis/SKILL.md"
    assert qgis["source_url"] == "https://github.com/HKUDS/CLI-Anything/tree/main/QGIS/agent-harness"


@pytest.mark.parametrize("skill", [
    "skills/foo/SKILL.md typo", "https-not-a-url", "npx not-a-skill-command",
    "QGIS/agent-harness/cli_anything/qgis/skills/SKILL.md",
])
def test_skill_validation_rejects_invalid_registry_values(monkeypatch, skill):
    import sys
    monkeypatch.setattr(sys.modules[__name__], "_load", lambda _: [{"name": "qgis", "skill_md": skill}])
    with pytest.raises(AssertionError):
        test_in_repo_skill_md_paths_exist("registry.json")


@pytest.mark.parametrize("strategy", ["command", "uv", "pip"])
def test_public_install_strategy_requires_command(monkeypatch, strategy):
    import sys
    cli = dict(_load("public_registry.json")[0], install_strategy=strategy)
    cli.pop("install_cmd", None)
    monkeypatch.setattr(sys.modules[__name__], "_load", lambda _: [cli])
    with pytest.raises(AssertionError):
        test_required_fields_and_contributors_shape("public_registry.json")


@pytest.mark.parametrize("url", [
    "https://unrelated.example/other/branch/QGIS/agent-harness",
    "https://github.com/HKUDS/CLI-Anything/tree/wrong-branch/QGIS/agent-harness",
])
def test_qgis_source_url_rejects_unrelated_host(monkeypatch, url):
    import sys
    qgis = next(c for c in _load("registry.json") if c["name"] == "qgis")
    qgis = dict(qgis, source_url=url)
    monkeypatch.setattr(sys.modules[__name__], "_load", lambda _: [qgis])
    with pytest.raises(AssertionError):
        test_qgis_skill_md_is_canonical()


@pytest.mark.parametrize("name", ["android-cli", "obsidian-cli"])
def test_bundled_public_entry_does_not_require_install_command(monkeypatch, name):
    import sys
    cli = next(c for c in _load("public_registry.json") if c["name"] == name)
    cli = dict(cli)
    cli.pop("install_cmd", None)
    monkeypatch.setattr(sys.modules[__name__], "_load", lambda _: [cli])
    test_required_fields_and_contributors_shape("public_registry.json")


@pytest.mark.parametrize("skill", [
    "https://example.com/a%20skill/SKILL.md", "npx skills add larksuite/cli -y -g",
])
def test_skill_validation_accepts_urls_and_explicit_skill_commands(monkeypatch, skill):
    import sys
    monkeypatch.setattr(sys.modules[__name__], "_load", lambda _: [{"name": "external", "skill_md": skill}])
    test_in_repo_skill_md_paths_exist("public_registry.json")


@pytest.mark.parametrize("source_url", [
    None,
    "https://github.com/HKUDS/CLI-Anything/tree/main/gimp/agent-harness",
    "https://github.com/HKUDS/CLI-Anything.git/tree/main/gimp/agent-harness",
    "https://GITHUB.COM/HKUDS/CLI-Anything/tree/main/gimp/agent-harness",
])
@pytest.mark.parametrize("skill", ["https://unrelated.example/SKILL.md", "npx skills add untrusted/skill"])
def test_in_repo_entries_cannot_bypass_local_skill_validation(monkeypatch, source_url, skill):
    import sys
    entry = {"name": "gimp", "source_url": source_url, "skill_md": skill}
    monkeypatch.setattr(sys.modules[__name__], "_load", lambda _: [entry])
    with pytest.raises(AssertionError):
        test_in_repo_skill_md_paths_exist("registry.json")


def test_standalone_registry_entry_can_reference_remote_skill(monkeypatch):
    import sys
    entry = {"name": "external", "source_url": "https://github.com/other/standalone", "skill_md": "https://example.com/SKILL.md"}
    monkeypatch.setattr(sys.modules[__name__], "_load", lambda _: [entry])
    test_in_repo_skill_md_paths_exist("registry.json")


@pytest.mark.parametrize("package", [None, "", "  ", False, 7])
def test_npm_strategy_rejects_invalid_package_metadata(monkeypatch, package):
    import sys
    cli = dict(_load("public_registry.json")[0], package_manager="npm")
    cli.pop("install_strategy", None)
    cli.pop("install_cmd", None)
    if package is None:
        cli.pop("npm_package", None)
    else:
        cli["npm_package"] = package
    monkeypatch.setattr(sys.modules[__name__], "_load", lambda _: [cli])
    with pytest.raises(AssertionError):
        test_required_fields_and_contributors_shape("public_registry.json")


def test_npm_strategy_accepts_its_package_without_install_command(monkeypatch):
    import sys
    cli = dict(_load("public_registry.json")[0], package_manager="npm", npm_package="@example/cli")
    cli.pop("install_strategy", None)
    cli.pop("install_cmd", None)
    monkeypatch.setattr(sys.modules[__name__], "_load", lambda _: [cli])
    test_required_fields_and_contributors_shape("public_registry.json")


@pytest.mark.parametrize("name,skill", [
    ("gimp", "skills/cli-anything-qgis/SKILL.md"),
    ("cc-switch", "skills/cli-anything-qgis/SKILL.md"),
    ("slay_the_spire_ii", "skills/cli-anything-gimp/SKILL.md"),
    ("unimol_tools", "skills/cli-anything-gimp/SKILL.md"),
    ("calibre", "skills/cli-anything-gimp/SKILL.md"),
])
def test_local_skill_cannot_reference_another_entry(monkeypatch, name, skill):
    import sys
    monkeypatch.setattr(sys.modules[__name__], "_load", lambda _: [{"name": name, "skill_md": skill}])
    with pytest.raises(AssertionError):
        test_in_repo_skill_md_paths_exist("registry.json")


@pytest.mark.parametrize("field", [
    "name", "display_name", "version", "description", "homepage", "entry_point", "category",
])
@pytest.mark.parametrize("value", [None, "", "  ", False, 7, [], {}])
def test_required_text_fields_reject_non_text_and_blank_values(monkeypatch, field, value):
    import sys
    entry = dict(_load("registry.json")[0], **{field: value})
    monkeypatch.setattr(sys.modules[__name__], "_load", lambda _: [entry])
    with pytest.raises(AssertionError, match=field):
        test_required_fields_and_contributors_shape("registry.json")


@pytest.mark.parametrize("field", ["requires", "source_url", "skill_md"])
@pytest.mark.parametrize("value", [False, 7, [], {}])
def test_nullable_text_fields_reject_non_text_values(monkeypatch, field, value):
    import sys
    entry = dict(_load("registry.json")[0], **{field: value})
    monkeypatch.setattr(sys.modules[__name__], "_load", lambda _: [entry])
    with pytest.raises(AssertionError, match=field):
        test_required_fields_and_contributors_shape("registry.json")


@pytest.mark.parametrize("contributor", [None, "author", {}, {"name": 7, "url": "https://example.com"},
                                        {"name": "author", "url": False}, {"name": "  ", "url": "https://example.com"}])
def test_contributors_require_objects_with_nonblank_text(monkeypatch, contributor):
    import sys
    entry = dict(_load("registry.json")[0], contributors=[contributor])
    monkeypatch.setattr(sys.modules[__name__], "_load", lambda _: [entry])
    with pytest.raises(AssertionError, match="contributor"):
        test_required_fields_and_contributors_shape("registry.json")


def test_nullable_metadata_remains_usable_by_real_registry_consumers(monkeypatch):
    import sys
    from cli_hub import registry
    entry = dict(_load("registry.json")[0], requires=None, source_url=None, skill_md=None)
    monkeypatch.setattr(sys.modules[__name__], "_load", lambda _: [entry])
    test_required_fields_and_contributors_shape("registry.json")
    monkeypatch.setattr(registry, "fetch_all_clis", lambda *args: [entry])
    assert registry.get_cli(entry["name"].upper()) == entry
    assert registry.search_clis(entry["description"]) == [entry]
    assert registry.search_clis("no-such-registry-search-token") == []
    assert registry.list_categories() == [entry["category"]]


@pytest.mark.parametrize("field", ["name", "description", "category", "display_name"])
def test_text_contract_guards_real_search_consumer(monkeypatch, field):
    import sys
    from cli_hub import registry
    entry = dict(_load("registry.json")[0], **{field: None})
    monkeypatch.setattr(registry, "fetch_all_clis", lambda *args: [entry])
    # The consumer really dereferences these strings; this is not an invented schema rule.
    with pytest.raises(AttributeError):
        registry.search_clis("no-such-registry-search-token")
    monkeypatch.setattr(sys.modules[__name__], "_load", lambda _: [entry])
    with pytest.raises(AssertionError, match=field):
        test_required_fields_and_contributors_shape("registry.json")
