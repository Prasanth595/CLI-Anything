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

    # Resolve only branches that can contain the parent or new sibling.
    try:
        parent = _normalize_node_path(parent)
        new_path = _normalize_node_path(node_name if parent == "." else f"{parent}/{node_name}")
        paths = _scene_node_paths(Path(project_path).resolve(), full_path, set(), {parent, new_path})
    except (OSError, ValueError) as exc:
        return {"status": "error", "message": f"Cannot verify scene hierarchy: {exc}"}
    if parent not in paths:
        return {"status": "error", "message": f"Parent node not found in scene: {parent}"}
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

def _normalize_node_path(value: str) -> str:
    """Canonical root-relative NodePath used for membership and duplicate checks."""
    if value.startswith("/") or ":" in value:
        raise ValueError(f"Expected a relative node path: {value!r}")
    parts = []
    for part in value.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                raise ValueError(f"Node path escapes the scene root: {value!r}")
            parts.pop()
        else:
            parts.append(part)
    return "/".join(parts) or "."


def _scene_node_paths(
    project_root: Path, scene_path: Path, ancestors: set[Path], queries: set[str],
) -> set[str]:
    """Resolve queried TSCN branches; reject unknown descendants without decoding them."""
    import re
    scene_path = scene_path.resolve()
    if not scene_path.is_relative_to(project_root):
        raise ValueError("PackedScene path escapes the project")
    if scene_path in ancestors or len(ancestors) >= 64:
        raise ValueError("Cyclic or too-deep PackedScene hierarchy")
    if scene_path.suffix.lower() != ".tscn":
        raise ValueError(f"Unsupported PackedScene format: {scene_path.suffix}")
    ancestors = ancestors | {scene_path}
    headers = [header for line in scene_path.read_text(encoding="utf-8").splitlines()
               if (header := _parse_section_header(line.strip())) is not None]
    resources = {attrs.get("id"): attrs for kind, attrs in headers if kind == "ext_resource"}
    nodes = [attrs for kind, attrs in headers if kind == "node"]
    if sum("parent" not in node for node in nodes) != 1:
        raise ValueError("Scene must declare exactly one root node")
    paths = set()
    parents = []
    for node in nodes:
        name = node.get("name")
        if not name:
            raise ValueError("Scene node has no name")
        parent = node.get("parent")
        if parent is None:
            node_path = "."
        else:
            parent = _normalize_node_path(parent)
            node_path = _normalize_node_path(f"{parent}/{name}")
        paths.add(node_path)
        if parent is not None and any(query == node_path or query.startswith(node_path + "/") for query in queries):
            parents.append(parent)
        nested_queries = {
            query if node_path == "." else query[len(node_path) + 1:]
            for query in queries
            if node_path == "." or query.startswith(node_path + "/")
        }
        if "instance" not in node or not nested_queries:
            continue
        match = re.fullmatch(r'ExtResource\s*\(\s*"([^"\\]+)"\s*\)', node["instance"])
        resource = resources.get(match.group(1)) if match else None
        if not resource or resource.get("type") != "PackedScene":
            raise ValueError(f"Cannot resolve PackedScene instance at {node_path}")
        resource_path = resource.get("path", "")
        if not resource_path.startswith("res://"):
            raise ValueError(f"Unsupported PackedScene path: {resource_path!r}")
        inherited = _scene_node_paths(project_root, project_root / resource_path[6:], ancestors, nested_queries)
        for child in inherited:
            paths.add(node_path if child == "." else _normalize_node_path(f"{node_path}/{child}"))
    if any(parent not in paths for parent in parents):
        raise ValueError("Scene contains an unresolved parent node")
    return paths


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
    for match in re.finditer(r'(\w+)\s*=\s*(?:"((?:\\.|[^"\\])*)"|((?:ExtResource|SubResource)\s*\(\s*"[^"]*"\s*\)|[^\s]+))', attr_string):
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
