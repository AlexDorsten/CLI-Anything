"""
Macro generation module for the FreeCAD CLI harness.

Generates complete FreeCAD Python macro scripts from JSON project state.
The generated scripts can be executed headlessly via ``FreeCADCmd`` to
create geometry and export to various CAD/mesh formats.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Safe name helper
# ---------------------------------------------------------------------------


def _safe_name(name: str) -> str:
    """Convert a user-supplied name into a valid FreeCAD object label.

    Replaces non-alphanumeric characters with underscores and ensures the
    name does not start with a digit.
    """
    safe = re.sub(r"[^A-Za-z0-9_]", "_", name)
    if safe and safe[0].isdigit():
        safe = f"_{safe}"
    return safe or "Unnamed"


# ---------------------------------------------------------------------------
# Internal generators
# ---------------------------------------------------------------------------


def _gen_header() -> List[str]:
    """Generate import statements and document creation."""
    return [
        "# Auto-generated FreeCAD macro by CLI-Anything FreeCAD harness",
        "import sys",
        "import os",
        "import FreeCAD",
        "import Part",
        "",
        "doc = FreeCAD.newDocument('ExportDoc')",
        "",
    ]


_RENDERABLE_PRIMITIVES = {"box", "cylinder", "sphere", "cone", "torus"}


def _emit_primitive(lines: List[str], part_type: str, name: str, props: Dict[str, Any]) -> bool:
    """Append FreeCAD object creation lines for a supported primitive."""
    if part_type == "box":
        length = props.get("length", props.get("Length", 10.0))
        width = props.get("width", props.get("Width", 10.0))
        height = props.get("height", props.get("Height", 10.0))
        lines.append(f"obj_{name} = doc.addObject('Part::Box', '{name}')")
        lines.append(f"obj_{name}.Length = {length}")
        lines.append(f"obj_{name}.Width = {width}")
        lines.append(f"obj_{name}.Height = {height}")
        return True

    if part_type == "cylinder":
        radius = props.get("radius", props.get("Radius", 5.0))
        height = props.get("height", props.get("Height", 10.0))
        lines.append(f"obj_{name} = doc.addObject('Part::Cylinder', '{name}')")
        lines.append(f"obj_{name}.Radius = {radius}")
        lines.append(f"obj_{name}.Height = {height}")
        return True

    if part_type == "sphere":
        radius = props.get("radius", props.get("Radius", 5.0))
        lines.append(f"obj_{name} = doc.addObject('Part::Sphere', '{name}')")
        lines.append(f"obj_{name}.Radius = {radius}")
        return True

    if part_type == "cone":
        radius1 = props.get("radius1", props.get("Radius1", 5.0))
        radius2 = props.get("radius2", props.get("Radius2", 0.0))
        height = props.get("height", props.get("Height", 10.0))
        lines.append(f"obj_{name} = doc.addObject('Part::Cone', '{name}')")
        lines.append(f"obj_{name}.Radius1 = {radius1}")
        lines.append(f"obj_{name}.Radius2 = {radius2}")
        lines.append(f"obj_{name}.Height = {height}")
        return True

    if part_type == "torus":
        radius1 = props.get("radius1", props.get("Radius1", 10.0))
        radius2 = props.get("radius2", props.get("Radius2", 2.0))
        lines.append(f"obj_{name} = doc.addObject('Part::Torus', '{name}')")
        lines.append(f"obj_{name}.Radius1 = {radius1}")
        lines.append(f"obj_{name}.Radius2 = {radius2}")
        return True

    return False


def _part_by_id(project: dict, part_id: Any) -> Optional[Dict[str, Any]]:
    """Return the part payload matching *part_id*, if present."""
    for part in project.get("parts", []):
        if part.get("id") == part_id:
            return part
    return None


def _mirrored_position(position: Any, plane: str) -> List[float]:
    """Return a mirrored position vector for a simple plane reflection."""
    if isinstance(position, (list, tuple)):
        coords = [float(position[idx]) if len(position) > idx else 0.0 for idx in range(3)]
    else:
        coords = [
            float(position.get("x", 0.0)),
            float(position.get("y", 0.0)),
            float(position.get("z", 0.0)),
        ]
    axis_index = {"YZ": 0, "XZ": 1, "XY": 2}.get(plane.upper())
    if axis_index is not None:
        coords[axis_index] *= -1.0
    return coords


def _mirror_render_spec(project: dict, part: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Resolve a mirrored part into a renderable primitive approximation."""
    params = part.get("params", {})
    original = _part_by_id(project, params.get("original_id"))
    if not original:
        return None
    original_type = str(original.get("type", "")).lower()
    if original_type not in _RENDERABLE_PRIMITIVES:
        return None

    placement = dict(original.get("placement") or {})
    placement["position"] = _mirrored_position(
        placement.get("position") or [0.0, 0.0, 0.0],
        str(params.get("mirror_plane", "XZ")),
    )
    return {
        "type": original_type,
        "params": dict(original.get("params") or {}),
        "placement": placement,
    }


def _render_spec_for_part(project: dict, part: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return the renderable primitive spec for *part*, if supported."""
    part_type = str(part.get("type", "")).lower()
    if part_type in _RENDERABLE_PRIMITIVES:
        return {
            "type": part_type,
            "params": dict(part.get("params") or {}),
            "placement": dict(part.get("placement") or {}),
        }
    if part_type == "mirror":
        return _mirror_render_spec(project, part)
    return None


def _gen_parts(project: dict) -> List[str]:
    """Generate Part primitives (Box, Cylinder, Sphere, Cone, Torus)."""
    lines: List[str] = []
    parts = project.get("parts", [])

    for part in parts:
        part_type = str(part.get("type", "box")).lower()
        name = _safe_name(part.get("name", f"Part_{part_type}"))
        render_spec = _render_spec_for_part(project, part)
        props = render_spec["params"] if render_spec else part.get("params", part.get("properties", {}))

        if render_spec and _emit_primitive(lines, render_spec["type"], name, props):
            pass
        else:
            lines.append(f"# WARNING: Unknown part type '{part_type}' for '{name}'")

        lines.append("")

    return lines


def _gen_boolean_ops(project: dict) -> List[str]:
    """Generate boolean operations (Cut, Fuse, Common)."""
    lines: List[str] = []
    boolean_ops = project.get("boolean_ops", [])

    # Map user-friendly names to FreeCAD object types
    op_type_map = {
        "cut": "Part::Cut",
        "subtract": "Part::Cut",
        "fuse": "Part::Fuse",
        "union": "Part::Fuse",
        "common": "Part::Common",
        "intersect": "Part::Common",
        "intersection": "Part::Common",
    }

    for op in boolean_ops:
        op_type = op.get("type", "fuse").lower()
        name = _safe_name(op.get("name", f"BoolOp_{op_type}"))
        base_name = _safe_name(op.get("base", ""))
        tool_name = _safe_name(op.get("tool", ""))
        fc_type = op_type_map.get(op_type, "Part::Fuse")

        lines.append(f"obj_{name} = doc.addObject('{fc_type}', '{name}')")
        lines.append(f"obj_{name}.Base = doc.getObject('{base_name}')")
        lines.append(f"obj_{name}.Tool = doc.getObject('{tool_name}')")
        lines.append("")

    return lines


def _placement_expr(placement: Optional[Dict[str, Any]]) -> Optional[str]:
    """Return a FreeCAD placement expression for a stored placement payload."""
    if not placement:
        return None
    position = placement.get("position") or [0.0, 0.0, 0.0]
    x = float(position[0] if len(position) > 0 else 0.0)
    y = float(position[1] if len(position) > 1 else 0.0)
    z = float(position[2] if len(position) > 2 else 0.0)
    # Axis-angle form takes precedence: it rotates the primitive's default +Z
    # axis onto an arbitrary measured direction (used for oblique bosses like a
    # tilted tube). Falls back to the Euler [rx, ry, rz] form otherwise.
    axis = placement.get("rotation_axis")
    if axis:
        ax = float(axis[0] if len(axis) > 0 else 0.0)
        ay = float(axis[1] if len(axis) > 1 else 0.0)
        az = float(axis[2] if len(axis) > 2 else 1.0)
        angle = float(placement.get("rotation_angle") or 0.0)
        rotation_expr = f"FreeCAD.Rotation(FreeCAD.Vector({ax}, {ay}, {az}), {angle})"
    else:
        rotation = placement.get("rotation") or [0.0, 0.0, 0.0]
        rx = float(rotation[0] if len(rotation) > 0 else 0.0)
        ry = float(rotation[1] if len(rotation) > 1 else 0.0)
        rz = float(rotation[2] if len(rotation) > 2 else 0.0)
        rotation_expr = f"FreeCAD.Rotation({rz}, {ry}, {rx})"
    return (
        "FreeCAD.Placement("
        f"FreeCAD.Vector({x}, {y}, {z}), "
        f"{rotation_expr})"
    )


# Sketch plane placements follow the FreeCAD body-origin conventions:
# XY is identity (normal +z), XZ is rotated 90 deg about +x (normal -y),
# YZ is rotated 120 deg about (1,1,1) (normal +x). The stored sketch
# ``offset`` is applied along the plane normal.
_SKETCH_PLANE_PLACEMENTS = {
    "XY": ("FreeCAD.Rotation()", (0.0, 0.0, 1.0)),
    "XZ": ("FreeCAD.Rotation(FreeCAD.Vector(1, 0, 0), 90)", (0.0, -1.0, 0.0)),
    "YZ": ("FreeCAD.Rotation(FreeCAD.Vector(1, 1, 1), 120)", (1.0, 0.0, 0.0)),
}


def _profile_sketch(project: dict, feat: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Resolve the sketch referenced by a sketch-based feature, if any."""
    sketch_index = feat.get("sketch_index")
    if not isinstance(sketch_index, int):
        return None
    sketches = project.get("sketches", [])
    if sketch_index < 0 or sketch_index >= len(sketches):
        return None
    sketch = sketches[sketch_index]
    return sketch if isinstance(sketch, dict) else None


def _emit_profile_sketch(
    lines: List[str],
    body_var: str,
    sketch: Dict[str, Any],
    sketch_var: str,
) -> bool:
    """Emit a Sketcher::SketchObject inside the body for a feature profile.

    Renders line and circle elements (the closed-outline building blocks the
    sketch CLI records); other element types produce a warning comment. Returns
    True when at least one geometry element was emitted, so the caller only
    binds Profile to sketches that can actually form a face.
    """
    elements = sketch.get("elements", [])
    supported = [
        element
        for element in elements
        if isinstance(element, dict) and element.get("type") in {"line", "circle"}
    ]
    if not supported:
        return False

    sketch_name = _safe_name(sketch.get("name", "ProfileSketch"))
    plane = str(sketch.get("plane", "XY")).upper()
    rotation_expr, normal = _SKETCH_PLANE_PLACEMENTS.get(plane, _SKETCH_PLANE_PLACEMENTS["XY"])
    offset = float(sketch.get("offset", 0.0))
    origin = tuple(component * offset for component in normal)
    lines.append(f"{sketch_var} = {body_var}.newObject('Sketcher::SketchObject', '{sketch_name}')")
    lines.append(
        f"{sketch_var}.Placement = FreeCAD.Placement("
        f"FreeCAD.Vector({origin[0]}, {origin[1]}, {origin[2]}), {rotation_expr})"
    )
    for element in elements:
        element_type = element.get("type") if isinstance(element, dict) else None
        if element_type == "line":
            start = element.get("start", [0.0, 0.0])
            end = element.get("end", [0.0, 0.0])
            lines.append(
                f"{sketch_var}.addGeometry(Part.LineSegment("
                f"FreeCAD.Vector({float(start[0])}, {float(start[1])}, 0), "
                f"FreeCAD.Vector({float(end[0])}, {float(end[1])}, 0)), False)"
            )
        elif element_type == "circle":
            center = element.get("center", [0.0, 0.0])
            radius = float(element.get("radius", 1.0))
            lines.append(
                f"{sketch_var}.addGeometry(Part.Circle("
                f"FreeCAD.Vector({float(center[0])}, {float(center[1])}, 0), "
                f"FreeCAD.Vector(0, 0, 1), {radius}), False)"
            )
        else:
            lines.append(
                f"# WARNING: Skipping unsupported sketch element type '{element_type}' in '{sketch_name}'"
            )
    return True


def _dominant_axis(direction: Any) -> tuple[str, bool, bool]:
    """Resolve a direction vector to the closest body-origin axis."""
    if not isinstance(direction, (list, tuple)) or len(direction) != 3:
        return ("X", False, False)
    values = [float(component) for component in direction]
    axis_index = max(range(3), key=lambda idx: abs(values[idx]))
    axis_name = "XYZ"[axis_index]
    reversed_axis = values[axis_index] < 0
    off_axis = any(abs(value) > 1e-9 for idx, value in enumerate(values) if idx != axis_index)
    return (axis_name, reversed_axis, off_axis)


def _gen_bodies(project: dict) -> List[str]:
    """Generate PartDesign bodies with primitive and pattern features."""
    lines: List[str] = []
    bodies = project.get("bodies", [])

    if not bodies:
        return lines

    lines.append("import PartDesign")
    lines.append("import Sketcher")
    lines.append("")
    lines.extend(
        [
            "def _body_origin_ref(body_obj, role):",
            "    for origin_obj in body_obj.Origin.OriginFeatures:",
            "        if getattr(origin_obj, 'Role', None) == role:",
            "            return origin_obj",
            "    raise RuntimeError(f'Could not resolve body origin role: {role}')",
            "",
            "def _finishing_edges(shape, spec):",
            "    if spec == 'all':",
            "        return ['Edge%d' % (index + 1) for index in range(len(shape.Edges))]",
            "    if isinstance(spec, (list, tuple)):",
            "        return ['Edge%d' % int(index) for index in spec]",
            "    if spec in ('bottom_rim', 'top_rim'):",
            "        bound_box = shape.BoundBox",
            "        tolerance = max(bound_box.DiagonalLength * 1e-4, 1e-5)",
            "        plane_z = bound_box.ZMin if spec == 'bottom_rim' else bound_box.ZMax",
            "        # the rim is the OUTER perimeter of the rim-plane face only --",
            "        # never a hole or bore that happens to reach the same plane, and",
            "        # never bbox-side matching that misses a rounded/oval outline.",
            "        # Pick the largest-area planar face sitting in the rim plane and",
            "        # return its outer-wire edges; fall back to bbox-side edges when",
            "        # no such face is found (degenerate shells).",
            "        def _edge_name(edge):",
            "            for index, candidate in enumerate(shape.Edges):",
            "                if candidate.isSame(edge):",
            "                    return 'Edge%d' % (index + 1)",
            "            return None",
            "        rim_face = None",
            "        for face in shape.Faces:",
            "            if abs(face.CenterOfMass.z - plane_z) > tolerance:",
            "                continue",
            "            if rim_face is None or face.Area > rim_face.Area:",
            "                rim_face = face",
            "        names = []",
            "        if rim_face is not None:",
            "            seen = set()",
            "            for edge in rim_face.OuterWire.Edges:",
            "                name = _edge_name(edge)",
            "                if name is not None and name not in seen:",
            "                    seen.add(name)",
            "                    names.append(name)",
            "        if not names:",
            "            for index, edge in enumerate(shape.Edges):",
            "                vertexes = edge.Vertexes",
            "                if not vertexes:",
            "                    continue",
            "                if any(abs(v.Point.z - plane_z) > tolerance for v in vertexes):",
            "                    continue",
            "                center = edge.CenterOfMass",
            "                if (",
            "                    abs(center.x - bound_box.XMin) <= tolerance",
            "                    or abs(center.x - bound_box.XMax) <= tolerance",
            "                    or abs(center.y - bound_box.YMin) <= tolerance",
            "                    or abs(center.y - bound_box.YMax) <= tolerance",
            "                ):",
            "                    names.append('Edge%d' % (index + 1))",
            "        if not names:",
            "            raise RuntimeError('%s selected no edges' % spec)",
            "        return names",
            "    if spec == 'vertical_outer':",
            "        bound_box = shape.BoundBox",
            "        tolerance = max(bound_box.DiagonalLength * 1e-4, 1e-5)",
            "        corners = (",
            "            (bound_box.XMin, bound_box.YMin),",
            "            (bound_box.XMin, bound_box.YMax),",
            "            (bound_box.XMax, bound_box.YMin),",
            "            (bound_box.XMax, bound_box.YMax),",
            "        )",
            "        names = []",
            "        for index, edge in enumerate(shape.Edges):",
            "            vertexes = edge.Vertexes",
            "            if len(vertexes) < 2:",
            "                continue",
            "            first, last = vertexes[0].Point, vertexes[-1].Point",
            "            if abs(first.x - last.x) > tolerance or abs(first.y - last.y) > tolerance:",
            "                continue",
            "            if abs(first.z - last.z) <= tolerance:",
            "                continue",
            "            on_corner = any(",
            "                abs(first.x - corner_x) <= tolerance and abs(first.y - corner_y) <= tolerance",
            "                for corner_x, corner_y in corners",
            "            )",
            "            if on_corner:",
            "                names.append('Edge%d' % (index + 1))",
            "        if not names:",
            "            raise RuntimeError('vertical_outer selected no edges')",
            "        return names",
            "    raise RuntimeError('Unknown edge selector: %r' % (spec,))",
            "",
        ]
    )

    for body in bodies:
        body_name = _safe_name(body.get("name", "Body"))
        body_var = f"body_{body_name}"
        lines.append(f"{body_var} = doc.addObject('PartDesign::Body', '{body_name}')")

        features = body.get("features", [])
        previous_var: Optional[str] = None
        feature_counter = 0

        def emit_pattern(
            pattern_type: str,
            pattern_payload: Dict[str, Any],
            source_var: Optional[str],
            suffix: Optional[str] = None,
        ) -> Optional[str]:
            nonlocal feature_counter
            if source_var is None:
                lines.append(f"# WARNING: Cannot add {pattern_type} without a previous body feature")
                return None
            feature_counter += 1
            pattern_var = f"feat_{body_name}_{feature_counter}_{pattern_type}"
            label = _safe_name(f"{pattern_type}_{feature_counter}")
            if pattern_type == "linear_pattern":
                axis, reversed_axis, off_axis = _dominant_axis(pattern_payload.get("direction"))
                lines.append(
                    f"{pattern_var} = {body_var}.newObject('PartDesign::LinearPattern', '{label}')"
                )
                lines.append(f"{pattern_var}.Originals = [{source_var}]")
                lines.append(
                    f"{pattern_var}.Direction = (_body_origin_ref({body_var}, '{axis}_Axis'), [''])"
                )
                lines.append(f"{pattern_var}.Length = {float(pattern_payload.get('length', 50.0))}")
                lines.append(
                    f"{pattern_var}.Occurrences = {int(pattern_payload.get('occurrences', 3))}"
                )
                if reversed_axis:
                    lines.append(f"{pattern_var}.Reversed = True")
                if off_axis:
                    lines.append(
                        f"# WARNING: Non-axis-aligned direction {pattern_payload.get('direction')} "
                        f"collapsed to dominant {axis}-axis"
                    )
            elif pattern_type == "polar_pattern":
                axis = str(pattern_payload.get("axis", "Z")).upper()
                lines.append(
                    f"{pattern_var} = {body_var}.newObject('PartDesign::PolarPattern', '{label}')"
                )
                lines.append(f"{pattern_var}.Originals = [{source_var}]")
                lines.append(
                    f"{pattern_var}.Axis = (_body_origin_ref({body_var}, '{axis}_Axis'), [''])"
                )
                lines.append(f"{pattern_var}.Angle = {float(pattern_payload.get('angle', 360.0))}")
                lines.append(
                    f"{pattern_var}.Occurrences = {int(pattern_payload.get('occurrences', 4))}"
                )
            elif pattern_type == "mirrored":
                plane = str(pattern_payload.get("plane", "XY")).upper()
                lines.append(
                    f"{pattern_var} = {body_var}.newObject('PartDesign::Mirrored', '{label}')"
                )
                lines.append(f"{pattern_var}.Originals = [{source_var}]")
                lines.append(
                    f"{pattern_var}.MirrorPlane = (_body_origin_ref({body_var}, '{plane}_Plane'), [''])"
                )
            else:
                lines.append(f"# WARNING: Unknown pattern type '{pattern_type}' in {suffix or 'feature'}")
                return source_var
            lines.append("")
            return pattern_var

        primitive_map = {
            "additive_box": ("PartDesign::AdditiveBox", ("Length", "length"), ("Width", "width"), ("Height", "height")),
            "additive_cylinder": ("PartDesign::AdditiveCylinder", ("Radius", "radius"), ("Height", "height")),
            "additive_sphere": ("PartDesign::AdditiveSphere", ("Radius", "radius")),
            "additive_cone": ("PartDesign::AdditiveCone", ("Radius1", "radius1"), ("Radius2", "radius2"), ("Height", "height")),
            "additive_torus": ("PartDesign::AdditiveTorus", ("Radius1", "radius1"), ("Radius2", "radius2")),
            "additive_wedge": ("PartDesign::AdditiveWedge", ("Xmin", "xmin"), ("Xmax", "xmax"), ("Ymin", "ymin"), ("Ymax", "ymax"), ("Zmin", "zmin"), ("Zmax", "zmax"), ("X2min", "x2min"), ("X2max", "x2max"), ("Z2min", "z2min"), ("Z2max", "z2max")),
            "subtractive_box": ("PartDesign::SubtractiveBox", ("Length", "length"), ("Width", "width"), ("Height", "height")),
            "subtractive_cylinder": ("PartDesign::SubtractiveCylinder", ("Radius", "radius"), ("Height", "height")),
            "subtractive_sphere": ("PartDesign::SubtractiveSphere", ("Radius", "radius")),
            "subtractive_cone": ("PartDesign::SubtractiveCone", ("Radius1", "radius1"), ("Radius2", "radius2"), ("Height", "height")),
            "subtractive_torus": ("PartDesign::SubtractiveTorus", ("Radius1", "radius1"), ("Radius2", "radius2")),
            "subtractive_wedge": ("PartDesign::SubtractiveWedge", ("Xmin", "xmin"), ("Xmax", "xmax"), ("Ymin", "ymin"), ("Ymax", "ymax"), ("Zmin", "zmin"), ("Zmax", "zmax"), ("X2min", "x2min"), ("X2max", "x2max"), ("Z2min", "z2min"), ("Z2max", "z2max")),
        }

        for feat in features:
            feat_type = feat.get("type", "pad").lower()
            feat_name = _safe_name(feat.get("name", f"Feature_{feat_type}"))
            feat_props = feat.get("properties", {})
            feature_counter += 1
            feat_var = f"feat_{body_name}_{feature_counter}_{_safe_name(feat_type)}"

            if feat_type in primitive_map:
                class_name, *prop_pairs = primitive_map[feat_type]
                lines.append(f"{feat_var} = {body_var}.newObject('{class_name}', '{feat_name}')")
                for prop_name, key in prop_pairs:
                    value = feat.get(key, feat_props.get(key))
                    if value is not None:
                        lines.append(f"{feat_var}.{prop_name} = {float(value)}")
                placement_expr = _placement_expr(feat.get("placement") or feat_props.get("placement"))
                if placement_expr:
                    lines.append(f"{feat_var}.Placement = {placement_expr}")
                previous_var = feat_var

            elif feat_type == "linear_pattern":
                previous_var = emit_pattern("linear_pattern", feat, previous_var)

            elif feat_type == "polar_pattern":
                previous_var = emit_pattern("polar_pattern", feat, previous_var)

            elif feat_type == "mirrored":
                previous_var = emit_pattern("mirrored", feat, previous_var)

            elif feat_type == "multi_transform":
                transforms = feat.get("transformations", [])
                if not transforms:
                    lines.append(f"# WARNING: multi_transform '{feat_name}' has no transformations")
                for transform_index, transform in enumerate(transforms):
                    previous_var = emit_pattern(
                        str(transform.get("type", "")).lower(),
                        transform,
                        previous_var,
                        suffix=f"multi_transform[{transform_index}]",
                    )

            elif feat_type == "pad":
                length = feat.get("length", feat_props.get("length", feat_props.get("Length", 10.0)))
                profile_var: Optional[str] = None
                sketch = _profile_sketch(project, feat)
                if sketch is not None:
                    candidate_var = f"sketch_{body_name}_{feature_counter}"
                    if _emit_profile_sketch(lines, body_var, sketch, candidate_var):
                        profile_var = candidate_var
                    else:
                        lines.append(
                            f"# WARNING: Pad '{feat_name}' references sketch without supported geometry"
                        )
                elif feat.get("sketch_index") is not None:
                    lines.append(
                        f"# WARNING: Pad '{feat_name}' references unknown sketch index {feat.get('sketch_index')!r}"
                    )
                lines.append(
                    f"{feat_var} = {body_var}.newObject('PartDesign::Pad', '{feat_name}')"
                )
                if profile_var is not None:
                    lines.append(f"{feat_var}.Profile = {profile_var}")
                    lines.append(f"{profile_var}.Visibility = False")
                lines.append(f"{feat_var}.Length = {float(length)}")
                if feat.get("reversed"):
                    lines.append(f"{feat_var}.Reversed = True")
                if feat.get("symmetric"):
                    lines.append(f"{feat_var}.Midplane = True")
                previous_var = feat_var

            elif feat_type == "pocket":
                length = feat.get("length", feat_props.get("length", feat_props.get("Length", 5.0)))
                profile_var = None
                sketch = _profile_sketch(project, feat)
                if sketch is not None:
                    candidate_var = f"sketch_{body_name}_{feature_counter}"
                    if _emit_profile_sketch(lines, body_var, sketch, candidate_var):
                        profile_var = candidate_var
                    else:
                        lines.append(
                            f"# WARNING: Pocket '{feat_name}' references sketch without supported geometry"
                        )
                elif feat.get("sketch_index") is not None:
                    lines.append(
                        f"# WARNING: Pocket '{feat_name}' references unknown sketch index {feat.get('sketch_index')!r}"
                    )
                lines.append(
                    f"{feat_var} = {body_var}.newObject('PartDesign::Pocket', '{feat_name}')"
                )
                if profile_var is not None:
                    lines.append(f"{feat_var}.Profile = {profile_var}")
                    lines.append(f"{profile_var}.Visibility = False")
                lines.append(f"{feat_var}.Length = {float(length)}")
                if feat.get("reversed"):
                    lines.append(f"{feat_var}.Reversed = True")
                if feat.get("symmetric"):
                    lines.append(f"{feat_var}.Midplane = True")
                previous_var = feat_var

            elif feat_type == "revolution":
                angle = feat_props.get("angle", feat_props.get("Angle", 360.0))
                lines.append(
                    f"{feat_var} = {body_var}.newObject('PartDesign::Revolution', '{feat_name}')"
                )
                lines.append(f"{feat_var}.Angle = {angle}")
                previous_var = feat_var

            elif feat_type == "chamfer":
                size = feat.get("size", feat_props.get("size", feat_props.get("Size", 1.0)))
                edges_spec = feat.get("edges", feat_props.get("edges", "all"))
                if previous_var is None:
                    lines.append(f"# WARNING: Cannot chamfer '{feat_name}' without a previous body feature")
                else:
                    lines.append("doc.recompute()")
                    lines.append(
                        f"{feat_var} = {body_var}.newObject('PartDesign::Chamfer', '{feat_name}')"
                    )
                    lines.append(
                        f"{feat_var}.Base = ({previous_var}, _finishing_edges({previous_var}.Shape, {edges_spec!r}))"
                    )
                    lines.append(f"{feat_var}.Size = {size}")
                    previous_var = feat_var

            elif feat_type == "fillet":
                radius = feat.get("radius", feat_props.get("radius", feat_props.get("Radius", 1.0)))
                edges_spec = feat.get("edges", feat_props.get("edges", "all"))
                if previous_var is None:
                    lines.append(f"# WARNING: Cannot fillet '{feat_name}' without a previous body feature")
                else:
                    lines.append("doc.recompute()")
                    lines.append(
                        f"{feat_var} = {body_var}.newObject('PartDesign::Fillet', '{feat_name}')"
                    )
                    lines.append(
                        f"{feat_var}.Base = ({previous_var}, _finishing_edges({previous_var}.Shape, {edges_spec!r}))"
                    )
                    lines.append(f"{feat_var}.Radius = {radius}")
                    previous_var = feat_var

            else:
                lines.append(
                    f"# WARNING: Unknown feature type '{feat_type}' "
                    f"for '{feat_name}'"
                )

            lines.append("")

    return lines


def _gen_placements(project: dict) -> List[str]:
    """Generate placement (position and rotation) commands for parts."""
    lines: List[str] = []
    parts = project.get("parts", [])

    for part in parts:
        name = _safe_name(part.get("name", ""))
        render_spec = _render_spec_for_part(project, part)
        if render_spec is None:
            lines.append(f"# WARNING: Skipping placement for unsupported part '{name}'")
            lines.append("")
            continue
        placement = render_spec.get("placement", {})

        if not placement:
            continue

        position = placement.get("position", {})
        rotation = placement.get("rotation", {})

        # Support both list [x, y, z] and dict {"x": ..., "y": ..., "z": ...}
        if isinstance(position, (list, tuple)):
            x = position[0] if len(position) > 0 else 0.0
            y = position[1] if len(position) > 1 else 0.0
            z = position[2] if len(position) > 2 else 0.0
        else:
            x = position.get("x", 0.0)
            y = position.get("y", 0.0)
            z = position.get("z", 0.0)

        # Rotation: support list [rx, ry, rz] (Euler) or dict formats
        if isinstance(rotation, (list, tuple)):
            rx = rotation[0] if len(rotation) > 0 else 0.0
            ry = rotation[1] if len(rotation) > 1 else 0.0
            rz = rotation[2] if len(rotation) > 2 else 0.0
            if rx != 0.0 or ry != 0.0 or rz != 0.0:
                lines.append(
                    f"obj_{name}.Placement = FreeCAD.Placement("
                    f"FreeCAD.Vector({x}, {y}, {z}), "
                    f"FreeCAD.Rotation({rz}, {ry}, {rx}))"
                )
            else:
                lines.append(
                    f"obj_{name}.Placement.Base = FreeCAD.Vector({x}, {y}, {z})"
                )
        elif "axis" in rotation and "angle" in rotation:
            axis = rotation["axis"]
            ax = axis.get("x", 0.0)
            ay = axis.get("y", 0.0)
            az = axis.get("z", 1.0)
            angle = rotation["angle"]
            lines.append(
                f"obj_{name}.Placement = FreeCAD.Placement("
                f"FreeCAD.Vector({x}, {y}, {z}), "
                f"FreeCAD.Rotation(FreeCAD.Vector({ax}, {ay}, {az}), {angle}))"
            )
        elif any(k in rotation for k in ("yaw", "pitch", "roll")):
            yaw = rotation.get("yaw", 0.0)
            pitch = rotation.get("pitch", 0.0)
            roll = rotation.get("roll", 0.0)
            lines.append(
                f"obj_{name}.Placement = FreeCAD.Placement("
                f"FreeCAD.Vector({x}, {y}, {z}), "
                f"FreeCAD.Rotation({yaw}, {pitch}, {roll}))"
            )
        else:
            # Position only, no rotation
            lines.append(
                f"obj_{name}.Placement.Base = FreeCAD.Vector({x}, {y}, {z})"
            )

        lines.append("")

    return lines


def _gen_export(
    project: dict,
    output_path: str,
    export_format: str,
) -> List[str]:
    """Generate export commands for the specified format.

    Supported formats:
      - ``step`` / ``iges``: via ``Part.export()``
      - ``stl``: via ``Mesh.export()``
      - ``obj``: via ``Mesh.export()``
      - ``brep``: via ``Part.export()``
      - ``fcstd``: via ``doc.saveAs()``
    """
    lines: List[str] = []

    # Escape backslashes for Windows paths in the generated Python script
    safe_path = output_path.replace("\\", "/")

    # Recompute the document before exporting
    lines.append("doc.recompute()")
    lines.append("")

    # Collect all visible shape objects for export
    lines.append("# Collect all shape objects for export")
    lines.append("export_objects = []")
    lines.append("for obj in doc.Objects:")
    lines.append("    if hasattr(obj, 'Shape') and obj.Shape.isValid():")
    lines.append("        export_objects.append(obj)")
    lines.append("")

    fmt = export_format.lower()

    if fmt in ("step", "iges", "brep"):
        lines.append(f"Part.export(export_objects, '{safe_path}')")

    elif fmt in ("stl", "obj"):
        lines.append("import Mesh")
        lines.append(f"Mesh.export(export_objects, '{safe_path}')")

    elif fmt == "fcstd":
        lines.append(f"doc.saveAs('{safe_path}')")

    else:
        # Fallback to Part.export for unknown formats
        lines.append(f"# Unknown format '{fmt}', attempting Part.export")
        lines.append(f"Part.export(export_objects, '{safe_path}')")

    lines.append("")
    lines.append("print('Export complete:', os.path.abspath('{safe_path}'))")
    lines.append("")

    return lines


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_macro(
    project: dict,
    output_path: str,
    export_format: str = "step",
) -> str:
    """Generate a complete FreeCAD Python macro script from project state.

    The generated script, when executed by ``FreeCADCmd``, will:
      1. Create a new FreeCAD document.
      2. Add all parts/primitives defined in the project.
      3. Apply boolean operations.
      4. Create PartDesign bodies with features.
      5. Set placements (positions and rotations).
      6. Export to the requested format.

    Parameters
    ----------
    project : dict
        Project JSON state.  Expected top-level keys:

        - ``parts``: list of part definitions (type, name, properties,
          placement).
        - ``boolean_ops``: list of boolean operation definitions.
        - ``bodies``: list of PartDesign body definitions with features.

    output_path : str
        Destination file path for the export.
    export_format : str
        Target format: ``"step"``, ``"iges"``, ``"stl"``, ``"obj"``,
        ``"brep"``, or ``"fcstd"``.

    Returns
    -------
    str
        Complete Python macro script ready for execution by FreeCADCmd.
    """
    sections: List[List[str]] = [
        _gen_header(),
        _gen_parts(project),
        _gen_boolean_ops(project),
        _gen_bodies(project),
        _gen_placements(project),
        _gen_export(project, output_path, export_format),
    ]

    # Flatten all sections and join with newlines
    all_lines: List[str] = []
    for section in sections:
        all_lines.extend(section)

    return "\n".join(all_lines)
