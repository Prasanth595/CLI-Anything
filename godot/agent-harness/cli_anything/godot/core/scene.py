"""Godot scene management — create, read, and modify .tscn scene files."""

from pathlib import Path
from cli_anything.godot.utils.godot_backend import validate_project


def create_scene(
    project_path: str,
    scene_path: str,
    root_type: str = "Node2D",
    root_name: str | None = None,
) -> dict:
    """Create a new .tscn scene file with a root node.

    Args:
        project_path: Godot project directory.
        scene_path: Relative path for the scene (e.g. 'scenes/Main.tscn').
        root_type: Node type for root (Node2D, Node3D, Control, etc.).
        root_name: Name for the root node; defaults to filename stem.

    Returns:
        Dict with status and created file path.
    """
    if not validate_project(project_path):
        return {"status": "error", "message": f"Not a Godot project: {project_path}"}

    full_path = Path(project_path) / scene_path
    if full_path.exists():
        return {"status": "error", "message": f"Scene already exists: {scene_path}"}

    if root_name is None:
        root_name = full_path.stem

    full_path.parent.mkdir(parents=True, exist_ok=True)

    content = (
        f'[gd_scene format=3 uid="uid://{_generate_uid()}"]\n\n'
        f'[node name="{root_name}" type="{root_type}"]\n'
    )
    full_path.write_text(content, encoding="utf-8")

    return {
        "status": "ok",
        "scene_path": scene_path,
        "root_type": root_type,
        "root_name": root_name,
        "absolute_path": str(full_path.resolve()),
    }


def read_scene(project_path: str, scene_path: str) -> dict:
    """Parse a .tscn file and return its node tree structure.

    Args:
        project_path: Godot project directory.
        scene_path: Relative path to the .tscn file.

    Returns:
        Dict with scene structure (nodes, resources, connections).
    """
    full_path = Path(project_path) / scene_path
    if not full_path.exists():
        return {"status": "error", "message": f"Scene not found: {scene_path}"}

    text = full_path.read_text(encoding="utf-8")
    nodes = []
    ext_resources = []
    sub_resources = []
    connections = []

    current_section = None
    current_attrs = {}

    for line in text.splitlines():
        line = line.strip()
        if not line:
            if current_section and current_attrs:
                _store_section(current_section, current_attrs,
                               nodes, ext_resources, sub_resources, connections)
                current_section = None
                current_attrs = {}
            continue

        header = _parse_section_header(line)
        if header is not None:
            if current_section and current_attrs:
                _store_section(current_section, current_attrs,
                               nodes, ext_resources, sub_resources, connections)
            current_section, current_attrs = header
            continue

        if "=" in line and current_section:
            key, _, value = line.partition("=")
            current_attrs[key.strip()] = value.strip()

    if current_section and current_attrs:
        _store_section(current_section, current_attrs,
                       nodes, ext_resources, sub_resources, connections)

    return {
        "status": "ok",
        "scene_path": scene_path,
        "nodes": nodes,
        "ext_resources": ext_resources,
        "sub_resources": sub_resources,
        "connections": connections,
    }


def add_node(
    project_path: str,
    scene_path: str,
    node_name: str,
    node_type: str,
    parent: str = ".",
) -> dict:
    """Append a child node to an existing .tscn scene.

    Args:
        project_path: Godot project directory.
        scene_path: Relative path to the .tscn file.
        node_name: Name of the new node.
        node_type: Type of the node (Sprite2D, CollisionShape2D, etc.).
        parent: Parent node path (default '.' = root).

    Returns:
        Dict with status.
    """
    full_path = Path(project_path) / scene_path
    if not full_path.exists():
        return {"status": "error", "message": f"Scene not found: {scene_path}"}

    for label, value in (("node name", node_name), ("node type", node_type), ("parent", parent)):
        if not value or any(c in value for c in '"\\\n\r'):
            return {"status": "error", "message": f"Invalid {label}: {value!r}"}
    if "/" in node_name:
        return {"status": "error", "message": f"Invalid node name (contains '/'): {node_name!r}"}

    # Godot rejects scenes whose nodes reference a missing parent or reuse a
    # sibling name, so check against the nodes already in the file.
    existing = read_scene(project_path, scene_path)["nodes"]
    paths = set()
    for n in existing:
        n_parent = n.get("parent")
        if n_parent is None:
            paths.add(".")
        elif n_parent == ".":
            paths.add(n.get("name", ""))
        else:
            paths.add(f"{n_parent}/{n.get('name', '')}")
    if parent not in paths and not _under_instanced_node(full_path, parent):
        return {"status": "error", "message": f"Parent node not found in scene: {parent}"}
    new_path = node_name if parent == "." else f"{parent}/{node_name}"
    if new_path in paths:
        return {"status": "error", "message": f"Node already exists: {new_path}"}

    node_line = f'\n[node name="{node_name}" type="{node_type}" parent="{parent}"]\n'
    with open(full_path, "a", encoding="utf-8") as f:
        f.write(node_line)

    return {
        "status": "ok",
        "node_name": node_name,
        "node_type": node_type,
        "parent": parent,
    }


# ---------- internal helpers ----------

def _under_instanced_node(full_path: Path, parent: str) -> bool:
    """True if parent may be a node inherited from an instanced/base scene.

    Children supplied by an instanced PackedScene (or by the base of an
    inherited scene whose root has instance=...) are not listed in the file,
    so they cannot be validated against it.
    """
    instanced = []
    for line in full_path.read_text(encoding="utf-8").splitlines():
        header = _parse_section_header(line.strip())
        if header is None or header[0] != "node":
            continue
        attrs = header[1]
        if "instance" not in attrs:
            continue
        name = attrs.get("name")
        par = attrs.get("parent")
        if not name:
            continue
        if not par:
            instanced.append(".")
        elif par == ".":
            instanced.append(name)
        else:
            instanced.append(f"{par}/{name}")
    return any(i == "." or parent == i or parent.startswith(i + "/") for i in instanced)


def _generate_uid() -> str:
    """Generate a UID for scene files using uuid4 for uniqueness."""
    import uuid
    return uuid.uuid4().hex[:12]



def _parse_section_header(line: str) -> tuple[str, dict] | None:
    """Read a complete tag, retaining closing brackets inside quoted values."""
    if not (line.startswith("[") and line.endswith("]")):
        return None
    parts = line[1:-1].split(None, 1)
    if not parts:
        return None
    return parts[0], _parse_tag_attrs(parts[1] if len(parts) > 1 else "")


def _parse_tag_attrs(attr_string: str) -> dict:
    """Parse tag attributes like 'name="Foo" type="Node2D"'."""
    attrs = {}
    import re
    for match in re.finditer(r'(\w+)\s*=\s*(?:"((?:\\.|[^"\\])*)"|([^\s]+))', attr_string):
        attrs[match.group(1)] = match.group(2) if match.group(2) is not None else match.group(3)
    return attrs


def _store_section(section, attrs, nodes, ext_resources, sub_resources, connections):
    """Store parsed section into the appropriate list."""
    if section == "node":
        nodes.append(attrs)
    elif section == "ext_resource":
        ext_resources.append(attrs)
    elif section == "sub_resource":
        sub_resources.append(attrs)
    elif section == "connection":
        connections.append(attrs)
