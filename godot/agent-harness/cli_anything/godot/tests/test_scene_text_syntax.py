"""Scene syntax regressions exercised through the actual Godot harness CLI."""
import json

import pytest
from click.testing import CliRunner

from cli_anything.godot.godot_cli import cli


def invoke(project, *args):
    result = CliRunner().invoke(cli, ["--json", "-p", str(project), "scene", *args])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


@pytest.fixture
def project(tmp_path):
    (tmp_path / "project.godot").write_text('[application]\nconfig/name="Syntax"\n')
    return tmp_path


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize("suffix", [" ; root", ";root", "\t; comment with ] and \"quotes\""])
def test_commented_header_keeps_quoted_semicolon_and_original_bytes(project, suffix, newline):
    scene = project / "main.tscn"
    original = (
        '[gd_scene format=3]\n; standalone comment\n'
        '[node name="Root;]Scene" type="Node"]' + suffix + '\n'
        'metadata/unknown = {"key": "untouched; ] value"}\n'
        '[node name="Semi;]Child" type="Node" parent="."] ; child\n'
    )
    original = original.replace("\n", newline).encode("utf-8")
    scene.write_bytes(original)
    parsed = invoke(project, "read", "main.tscn")
    assert [node["name"] for node in parsed["nodes"]] == ["Root;]Scene", "Semi;]Child"]
    assert parsed["nodes"][0]["metadata/unknown"] == '{"key": "untouched; ] value"}'
    added = invoke(project, "add-node", "main.tscn", "--name", "Leaf", "--type", "Node", "--parent", "Semi;]Child")
    assert added["status"] == "ok"
    assert scene.read_bytes().startswith(original)


@pytest.mark.parametrize("encoded,name", [
    (r"\u0041", "A"), (r"\U000041", "A"), (r"\u00e9", "é"),
    (r"\U01f600", "😀"), (r"\uD83D\uDE00", "😀"), ("München", "München"),
])
def test_escaped_node_name_rejects_logical_duplicate_without_writing(project, encoded, name):
    scene = project / "main.tscn"
    original = '[gd_scene format=3]\n[node name="Root" type="Node"]\n' + f'[node name="{encoded}" type="Node" parent="."]\n'
    scene.write_text(original)
    assert invoke(project, "read", "main.tscn")["nodes"][1]["name"] == name
    result = invoke(project, "add-node", "main.tscn", "--name", name, "--type", "Node")
    assert result["status"] == "error"
    assert "already exists" in result["message"]
    assert scene.read_text() == original


def test_escaped_parent_resource_path_and_resource_id_resolve(project):
    (project / "child.tscn").write_text(
        '[gd_scene format=3]\n[node name="Child" type="Node"]\n'
        '[node name="Branch" type="Node" parent="."]\n'
        '[node name="Leaf" type="Node" parent="Br\\u0061nch"]\n'
    )
    scene = project / "main.tscn"
    original = (
        '[gd_scene load_steps=2 format=3]\n'
        '[ext_resource type="PackedScene" path="res://ch\\u0069ld.tscn" id="\\u0031"] ; resource\n'
        '[node name="Root" type="Node"]\n'
        '[node name="Sub" parent="." instance=ExtResource("\\u0031")] ; nested\n'
    )
    scene.write_text(original)
    result = invoke(project, "add-node", "main.tscn", "--name", "New", "--type", "Node", "--parent", "Sub/Branch/Leaf")
    assert result["status"] == "ok"
    assert scene.read_text().startswith(original)


@pytest.mark.parametrize("encoded,decoded", [
    (r'back\\slash', 'back\\slash'), (r'quote\";tail', 'quote";tail'),
    (r'\a\b\t\n\v\f\r', 'a\b\t\nv\f\r'), (r'\q', 'q'),
])
def test_read_scene_decodes_godot_escapes_without_python_literal_evaluation(project, encoded, decoded):
    scene = project / "main.tscn"
    scene.write_text(f'[gd_scene format=3]\n[node name="Root" type="Node" custom="{encoded}"] ; comment\n')
    assert invoke(project, "read", "main.tscn")["nodes"][0]["custom"] == decoded


@pytest.mark.parametrize("encoded", [
    r"\u004", r"\uZZZZ", r"\U00004", r"\U110000", r"\uD800", r"\uDC00",
    r"\uD800A", r"\uD800\uD800", r"\uD800\u0041",
])
def test_invalid_unicode_header_is_reported_without_writing(project, encoded):
    scene = project / "main.tscn"
    original = '[gd_scene format=3]\n[node name="Root" type="Node"]\n' + f'[node name="{encoded}" type="Node" parent="."]\n'
    scene.write_text(original)
    assert invoke(project, "read", "main.tscn")["status"] == "error"
    result = invoke(project, "add-node", "main.tscn", "--name", "Child", "--type", "Node")
    assert result["status"] == "error"
    assert scene.read_text() == original
