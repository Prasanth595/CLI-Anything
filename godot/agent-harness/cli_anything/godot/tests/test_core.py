"""Unit tests for cli-anything-godot — no Godot binary required.

Tests project management, scene I/O, and export preset parsing
using temporary directories and mock files.
"""

import json
import os
import tempfile
from pathlib import Path
from unittest import mock

import pytest
from click.testing import CliRunner

from cli_anything.godot.godot_cli import cli


# ── Fixtures ───────────────────────────────────────────────────────────

@pytest.fixture
def tmp_project(tmp_path):
    """Create a minimal Godot project in a temp directory."""
    project_file = tmp_path / "project.godot"
    project_file.write_text(
        '; Engine configuration file.\n\n'
        '[application]\n\n'
        'config/name="TestGame"\n'
        'config/features=PackedStringArray("4.4", "GL Compatibility")\n'
        'run/main_scene="res://scenes/Main.tscn"\n\n'
        '[rendering]\n\n'
        'renderer/rendering_method="gl_compatibility"\n',
        encoding="utf-8",
    )

    # Create some scene files
    scenes_dir = tmp_path / "scenes"
    scenes_dir.mkdir()
    (scenes_dir / "Main.tscn").write_text(
        '[gd_scene format=3 uid="uid://abc123"]\n\n'
        '[node name="Main" type="Node2D"]\n',
        encoding="utf-8",
    )
    (scenes_dir / "Level1.tscn").write_text(
        '[gd_scene format=3 uid="uid://def456"]\n\n'
        '[node name="Level1" type="Node3D"]\n\n'
        '[node name="Player" type="CharacterBody3D" parent="."]\n',
        encoding="utf-8",
    )

    # Create script files
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "player.gd").write_text(
        'extends CharacterBody3D\n\nfunc _ready():\n\tpass\n',
        encoding="utf-8",
    )

    # Create resource files
    (tmp_path / "icon.tres").write_text("", encoding="utf-8")

    return tmp_path


@pytest.fixture
def runner():
    return CliRunner()


# ── Project tests ──────────────────────────────────────────────────────

class TestProjectCreate:
    def test_create_new_project(self, runner, tmp_path):
        project_dir = tmp_path / "new_game"
        result = runner.invoke(cli, ["--json", "project", "create", str(project_dir)])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"] == "ok"
        assert data["project_name"] == "new_game"
        assert (project_dir / "project.godot").exists()

    def test_create_with_custom_name(self, runner, tmp_path):
        project_dir = tmp_path / "my_dir"
        result = runner.invoke(cli, [
            "--json", "project", "create", str(project_dir), "--name", "Cool Game"
        ])
        data = json.loads(result.output)
        assert data["status"] == "ok"
        assert data["project_name"] == "Cool Game"

    def test_create_duplicate_fails(self, runner, tmp_project):
        result = runner.invoke(cli, ["--json", "project", "create", str(tmp_project)])
        data = json.loads(result.output)
        assert data["status"] == "error"


class TestProjectInfo:
    def test_info_valid_project(self, runner, tmp_project):
        result = runner.invoke(cli, ["--json", "-p", str(tmp_project), "project", "info"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"] == "ok"
        assert data["name"] == "TestGame"
        assert "4.4" in data["features"]

    def test_info_invalid_project(self, runner, tmp_path):
        result = runner.invoke(cli, ["--json", "-p", str(tmp_path), "project", "info"])
        data = json.loads(result.output)
        assert data["status"] == "error"


class TestProjectList:
    def test_list_scenes(self, runner, tmp_project):
        result = runner.invoke(cli, ["--json", "-p", str(tmp_project), "project", "scenes"])
        data = json.loads(result.output)
        assert data["status"] == "ok"
        assert data["count"] == 2
        assert "scenes/Main.tscn" in data["scenes"]
        assert "scenes/Level1.tscn" in data["scenes"]

    def test_list_scripts(self, runner, tmp_project):
        result = runner.invoke(cli, ["--json", "-p", str(tmp_project), "project", "scripts"])
        data = json.loads(result.output)
        assert data["status"] == "ok"
        assert data["count"] == 1
        assert "scripts/player.gd" in data["scripts"]

    def test_list_resources(self, runner, tmp_project):
        result = runner.invoke(cli, ["--json", "-p", str(tmp_project), "project", "resources"])
        data = json.loads(result.output)
        assert data["status"] == "ok"
        assert data["count"] == 1


# ── Scene tests ────────────────────────────────────────────────────────

class TestSceneCreate:
    def test_create_scene(self, runner, tmp_project):
        result = runner.invoke(cli, [
            "--json", "-p", str(tmp_project),
            "scene", "create", "scenes/NewScene.tscn",
            "--root-type", "Node3D",
        ])
        data = json.loads(result.output)
        assert data["status"] == "ok"
        assert data["root_type"] == "Node3D"
        assert (tmp_project / "scenes" / "NewScene.tscn").exists()

    def test_create_duplicate_scene_fails(self, runner, tmp_project):
        result = runner.invoke(cli, [
            "--json", "-p", str(tmp_project),
            "scene", "create", "scenes/Main.tscn",
        ])
        data = json.loads(result.output)
        assert data["status"] == "error"


class TestSceneRead:
    def test_read_scene(self, runner, tmp_project):
        result = runner.invoke(cli, [
            "--json", "-p", str(tmp_project),
            "scene", "read", "scenes/Level1.tscn",
        ])
        data = json.loads(result.output)
        assert data["status"] == "ok"
        assert len(data["nodes"]) == 2
        assert data["nodes"][0]["name"] == "Level1"
        assert data["nodes"][1]["name"] == "Player"

    def test_read_nonexistent_scene(self, runner, tmp_project):
        result = runner.invoke(cli, [
            "--json", "-p", str(tmp_project),
            "scene", "read", "scenes/Nope.tscn",
        ])
        data = json.loads(result.output)
        assert data["status"] == "error"


class TestSceneAddNode:
    def test_add_node(self, runner, tmp_project):
        result = runner.invoke(cli, [
            "--json", "-p", str(tmp_project),
            "scene", "add-node", "scenes/Main.tscn",
            "--name", "Camera",
            "--type", "Camera2D",
        ])
        data = json.loads(result.output)
        assert data["status"] == "ok"
        assert data["node_name"] == "Camera"

        # Verify the node was added to the file
        content = (tmp_project / "scenes" / "Main.tscn").read_text()
        assert 'name="Camera"' in content
        assert 'type="Camera2D"' in content

    def _add(self, runner, project, name, parent=".", type_="Node2D"):
        result = runner.invoke(cli, [
            "--json", "-p", str(project),
            "scene", "add-node", "scenes/Level1.tscn",
            "--name", name, "--type", type_, "--parent", parent,
        ])
        return json.loads(result.output)

    def test_add_node_missing_parent_rejected(self, runner, tmp_project):
        before = (tmp_project / "scenes" / "Level1.tscn").read_text()
        data = self._add(runner, tmp_project, "Gun", parent="Nope")
        assert data["status"] == "error"
        assert (tmp_project / "scenes" / "Level1.tscn").read_text() == before

    def test_add_node_duplicate_sibling_rejected(self, runner, tmp_project):
        data = self._add(runner, tmp_project, "Player")
        assert data["status"] == "error"
        assert "already exists" in data["message"]

    def test_add_node_nested_parent(self, runner, tmp_project):
        assert self._add(runner, tmp_project, "Gun", parent="Player")["status"] == "ok"
        assert self._add(runner, tmp_project, "Muzzle", parent="Player/Gun")["status"] == "ok"
        # Same name under a different parent is fine; same parent is not.
        assert self._add(runner, tmp_project, "Gun", parent=".")["status"] == "ok"
        assert self._add(runner, tmp_project, "Gun", parent="Player")["status"] == "error"

    def test_add_node_rejects_quote_in_name(self, runner, tmp_project):
        data = self._add(runner, tmp_project, 'Bad"Name')
        assert data["status"] == "error"

    @pytest.mark.parametrize("bad", ["Trail\\", "A\\nB"])
    def test_add_node_rejects_backslash(self, runner, tmp_project, bad):
        before = (tmp_project / "scenes" / "Level1.tscn").read_text()
        assert self._add(runner, tmp_project, bad)["status"] == "error"
        assert self._add(runner, tmp_project, "Ok", type_="Node\\")["status"] == "error"
        assert (tmp_project / "scenes" / "Level1.tscn").read_text() == before

    def test_add_node_allows_children_of_instanced_scene(self, runner, tmp_project):
        scene = tmp_project / "scenes" / "Derived.tscn"
        scene.write_text(
            '[gd_scene load_steps=2 format=3]\n\n'
            '[ext_resource type="PackedScene" path="res://scenes/Level1.tscn" id="1"]\n\n'
            '[node name="Derived" instance=ExtResource("1")]\n'
        )
        result = runner.invoke(cli, [
            "--json", "-p", str(tmp_project), "scene", "add-node", "scenes/Derived.tscn",
            "--name", "Gun", "--type", "Node2D", "--parent", "Player",
        ])
        assert json.loads(result.output)["status"] == "ok"


    @pytest.mark.parametrize("parent", ["", 'Bad"Parent', "Bad\\Parent", "Bad\nParent", "Bad\rParent"])
    def test_add_node_rejects_unsafe_parent_in_inherited_scene(self, tmp_project, parent):
        from cli_anything.godot.core.scene import add_node

        scene = tmp_project / "scenes" / "Derived.tscn"
        before = '[node name="Derived" instance=ExtResource("1")]\n'
        scene.write_text(before)
        assert add_node(str(tmp_project), "scenes/Derived.tscn", "Child", "Node", parent)["status"] == "error"
        assert scene.read_text() == before

    @pytest.mark.parametrize("header,parent", [
        ('[node name = "Root" type = "Node"]', "."),
        ('[node name = "Derived" instance = ExtResource( "1" )]', "Player"),
        ('[node name="Derived]Scene" instance=ExtResource("1")]', "Player"),
        ('[node name="Root" type="Node"]\n[node name="Sub]Scene" parent="." instance=ExtResource("1")]', "Sub]Scene/Player"),
    ])
    def test_add_node_accepts_formatted_and_quoted_inherited_headers(self, tmp_project, header, parent):
        from cli_anything.godot.core.scene import add_node, read_scene

        scene = tmp_project / "scenes" / "Derived.tscn"
        scene.write_text('[ext_resource type="PackedScene" path="res://scenes/Level1.tscn" id="1"]\n' + header + "\n")
        result = add_node(str(tmp_project), "scenes/Derived.tscn", "Child", "Node", parent)
        assert result["status"] == "ok"
        assert read_scene(str(tmp_project), "scenes/Derived.tscn")["nodes"][-1] == {
            "name": "Child", "type": "Node", "parent": parent,
        }


    def test_body_property_does_not_make_a_node_instanced(self, tmp_project):
        from cli_anything.godot.core.scene import add_node

        scene = tmp_project / "scenes" / "Derived.tscn"
        before = '[node name="Root" type="Node"]\ninstance = ExtResource("1")\n'
        scene.write_text(before)
        result = add_node(str(tmp_project), "scenes/Derived.tscn", "Child", "Node", "Missing")
        assert result["status"] == "error"
        assert scene.read_text() == before


    @pytest.mark.parametrize("header,parent,name", [
        ('[node name="Derived" instance=ExtResource("1")]', "Nope", "Gun"),
        ('[node name="Derived" instance=ExtResource("1")]', ".", "Player"),
        ('[node name="Root" type="Node"]\n[node name="Sub" parent="." instance=ExtResource("1")]', "Sub/Nope", "Gun"),
        ('[node name="Root" type="Node"]\n[node name="Sub" parent="." instance=ExtResource("1")]', "Sub", "Player"),
    ])
    def test_inherited_hierarchy_rejects_missing_parents_and_duplicates(self, tmp_project, header, parent, name):
        from cli_anything.godot.core.scene import add_node
        scene = tmp_project / "scenes" / "Derived.tscn"
        before = '[ext_resource type="PackedScene" path="res://scenes/Level1.tscn" id="1"]\n' + header + "\n"
        scene.write_text(before)
        assert add_node(str(tmp_project), "scenes/Derived.tscn", name, "Node", parent)["status"] == "error"
        assert scene.read_text() == before

    @pytest.mark.parametrize("reference", ["res://scenes/missing.tscn", "res://scenes/Derived.tscn", "uid://unknown"])
    def test_unverifiable_inherited_hierarchy_is_not_written(self, tmp_project, reference):
        from cli_anything.godot.core.scene import add_node
        scene = tmp_project / "scenes" / "Derived.tscn"
        before = f'[ext_resource type="PackedScene" path="{reference}" id="1"]\n[node name="Derived" instance=ExtResource("1")]\n'
        scene.write_text(before)
        result = add_node(str(tmp_project), "scenes/Derived.tscn", "Child", "Node", ".")
        assert result["status"] == "error"
        assert "Cannot verify scene hierarchy" in result["message"]
        assert scene.read_text() == before

    @pytest.mark.parametrize("extension", ["glb", "gltf", "scn"])
    def test_imported_scene_only_blocks_edits_inside_its_branch(self, tmp_project, extension):
        from cli_anything.godot.core.scene import add_node

        scene = tmp_project / "scenes" / "Imported.tscn"
        (tmp_project / f"model.{extension}").write_bytes(b"\xff\x00binary")
        before = (
            f'[ext_resource type="PackedScene" path="res://model.{extension}" id="1"]\n'
            '[node name="Root" type="Node3D"]\n'
            '[node name="Model" parent="." instance=ExtResource("1")]\n'
        )
        scene.write_text(before)
        assert add_node(str(tmp_project), "scenes/Imported.tscn", "Sibling", "Node", ".")["status"] == "ok"
        published = scene.read_bytes()
        assert add_node(str(tmp_project), "scenes/Imported.tscn", "Model", "Node", ".")["status"] == "error"
        assert scene.read_bytes() == published
        result = add_node(str(tmp_project), "scenes/Imported.tscn", "Child", "Node", "Model")
        assert result["status"] == "error"
        assert "Unsupported PackedScene format" in result["message"]
        assert scene.read_bytes() == published

    def test_nested_tscn_branch_ignores_unrelated_imported_descendants(self, tmp_project):
        from cli_anything.godot.core.scene import add_node

        nested = tmp_project / "scenes/Nested.tscn"
        nested.write_text(
            '[ext_resource type="PackedScene" path="res://missing.glb" id="1"]\n'
            '[node name="Root" type="Node3D"]\n'
            '[node name="Model" parent="." instance=ExtResource("1")]\n'
            '[node name="Folder" type="Node" parent="."]\n'
        )
        scene = tmp_project / "scenes/Outer.tscn"
        scene.write_text(
            '[ext_resource type="PackedScene" path="res://scenes/Nested.tscn" id="1"]\n'
            '[node name="Root" type="Node"]\n'
            '[node name="Sub" parent="." instance=ExtResource("1")]\n'
        )
        assert add_node(str(tmp_project), "scenes/Outer.tscn", "Child", "Node", "Sub/Folder")["status"] == "ok"
        before = scene.read_bytes()
        assert add_node(str(tmp_project), "scenes/Outer.tscn", "Model", "Node", "Sub")["status"] == "error"
        assert add_node(str(tmp_project), "scenes/Outer.tscn", "Child", "Node", "Sub/Model")["status"] == "error"
        assert scene.read_bytes() == before

    def test_equivalent_parent_nodepaths_share_membership_and_duplicates(self, runner, tmp_project):
        from cli_anything.godot.core.scene import add_node
        assert self._add(runner, tmp_project, "Child", parent="./Player")["status"] == "ok"
        scene = tmp_project / "scenes/Level1.tscn"
        before = scene.read_text()
        assert add_node(str(tmp_project), "scenes/Level1.tscn", "Child", "Node", "Player/.")["status"] == "error"
        assert scene.read_text() == before
        assert add_node(str(tmp_project), "scenes/Level1.tscn", "Grandchild", "Node", "./Player/Child/..")["status"] == "ok"


# ── Export tests ───────────────────────────────────────────────────────

class TestExportPresets:
    def test_no_presets_file(self, runner, tmp_project):
        result = runner.invoke(cli, [
            "--json", "-p", str(tmp_project), "export", "presets"
        ])
        data = json.loads(result.output)
        assert data["status"] == "ok"
        assert data["count"] == 0

    def test_parse_presets(self, runner, tmp_project):
        presets_file = tmp_project / "export_presets.cfg"
        presets_file.write_text(
            '[preset.0]\n\n'
            'name="Windows Desktop"\n'
            'platform="Windows Desktop"\n'
            'export_path="build/game.exe"\n\n'
            '[preset.1]\n\n'
            'name="Linux"\n'
            'platform="Linux/X11"\n'
            'export_path="build/game.x86_64"\n',
            encoding="utf-8",
        )
        result = runner.invoke(cli, [
            "--json", "-p", str(tmp_project), "export", "presets"
        ])
        data = json.loads(result.output)
        assert data["count"] == 2
        assert data["presets"][0]["name"] == "Windows Desktop"
        assert data["presets"][1]["platform"] == "Linux/X11"


# ── Engine tests ───────────────────────────────────────────────────────

class TestEngineStatus:
    def test_engine_status_no_godot(self, runner):
        with mock.patch(
            "cli_anything.godot.utils.godot_backend.find_godot_binary",
            return_value=None,
        ):
            result = runner.invoke(cli, ["--json", "engine", "status"])
            data = json.loads(result.output)
            assert data["available"] is False

    def test_engine_status_found(self, runner):
        with mock.patch(
            "cli_anything.godot.godot_cli.find_godot_binary",
            return_value="/usr/bin/godot",
        ):
            with mock.patch(
                "cli_anything.godot.godot_cli.is_available",
                return_value=True,
            ):
                result = runner.invoke(cli, ["--json", "engine", "status"])
                data = json.loads(result.output)
                assert data["available"] is True
                assert data["binary"] == "/usr/bin/godot"


# ── Backend tests ──────────────────────────────────────────────────────

class TestBackend:
    def test_validate_project(self, tmp_project):
        from cli_anything.godot.utils.godot_backend import validate_project
        assert validate_project(str(tmp_project)) is True

    def test_validate_non_project(self, tmp_path):
        from cli_anything.godot.utils.godot_backend import validate_project
        assert validate_project(str(tmp_path)) is False

    def test_find_godot_binary_env(self):
        from cli_anything.godot.utils.godot_backend import find_godot_binary
        with mock.patch.dict(os.environ, {"GODOT_BIN": "python"}):
            # python is guaranteed to be on PATH
            result = find_godot_binary()
            assert result is not None


# ── Script validation tests ────────────────────────────────────────────

class TestValidateScript:
    """Regression tests for #335: Godot 4.6.x exits 0 on parse errors,
    so validate_script must also scan stderr for error markers."""

    @staticmethod
    def _godot_result(returncode=0, stdout="", stderr=""):
        return {"returncode": returncode, "stdout": stdout, "stderr": stderr}

    def test_parse_error_with_zero_returncode_is_invalid(self, tmp_project):
        from cli_anything.godot.core.script import validate_script
        stderr = (
            "SCRIPT ERROR: Parse Error: Expected end of statement after "
            'expression, found ":" instead.\n'
            "          at: GDScript::reload (res://scripts/player.gd:3)"
        )
        with mock.patch(
            "cli_anything.godot.core.script.run_godot",
            return_value=self._godot_result(returncode=0, stderr=stderr),
        ):
            result = validate_script(str(tmp_project), "scripts/player.gd")
        assert result["status"] == "ok"
        assert result["valid"] is False
        assert "Parse Error" in result["errors"]

    def test_clean_script_with_zero_stderr_is_valid(self, tmp_project):
        from cli_anything.godot.core.script import validate_script
        with mock.patch(
            "cli_anything.godot.core.script.run_godot",
            return_value=self._godot_result(returncode=0, stderr=""),
        ):
            result = validate_script(str(tmp_project), "scripts/player.gd")
        assert result["status"] == "ok"
        assert result["valid"] is True
        assert result["errors"] == ""

    def test_benign_stderr_noise_is_still_valid(self, tmp_project):
        from cli_anything.godot.core.script import validate_script
        stderr = "WARNING: Blend file import is enabled in the project settings.\n"
        with mock.patch(
            "cli_anything.godot.core.script.run_godot",
            return_value=self._godot_result(returncode=0, stderr=stderr),
        ):
            result = validate_script(str(tmp_project), "scripts/player.gd")
        assert result["valid"] is True
        assert result["errors"] == ""

    def test_nonzero_returncode_is_invalid(self, tmp_project):
        from cli_anything.godot.core.script import validate_script
        stderr = "Failed to load script res://scripts/player.gd\n"
        with mock.patch(
            "cli_anything.godot.core.script.run_godot",
            return_value=self._godot_result(returncode=1, stderr=stderr),
        ):
            result = validate_script(str(tmp_project), "scripts/player.gd")
        assert result["valid"] is False
        assert "Failed to load script" in result["errors"]


# ── CLI root tests ─────────────────────────────────────────────────────

class TestCLIRoot:
    def test_help(self, runner):
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "cli-anything-godot" in result.output

    def test_no_args_shows_help(self, runner):
        result = runner.invoke(cli, [])
        assert "cli-anything-godot" in result.output

    def test_json_flag(self, runner, tmp_project):
        result = runner.invoke(cli, ["--json", "-p", str(tmp_project), "project", "info"])
        data = json.loads(result.output)
        assert isinstance(data, dict)

    def test_human_output(self, runner, tmp_project):
        result = runner.invoke(cli, ["-p", str(tmp_project), "project", "info"])
        assert "TestGame" in result.output
