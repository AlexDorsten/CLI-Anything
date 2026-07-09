"""
Macro generation module for the FreeCAD CLI harness.

Generates complete FreeCAD Python macro scripts from JSON project state.
The generated scripts can be executed headlessly via ``FreeCADCmd`` to
create geometry and export to various CAD/mesh formats.
"""

from __future__ import annotations

import math
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


# ---------------------------------------------------------------------------
# Dimensioned sketch+pad/pocket generation for primitive PartDesign features.
#
# additive_cylinder / subtractive_cylinder / additive_box used to lower
# straight to a PartDesign primitive feature (AdditiveCylinder, Pocket-less
# SubtractiveCylinder, AdditiveBox) with a raw Placement -- geometrically
# correct, but leaving no sketch, no datum plane and no driving dimensions
# for a human to pick up later. When the feature carries no rotation, emit
# instead a named datum plane (offset from the body's XY plane by the
# feature's Z position -- editable, visible in the tree) with a dimensioned
# sketch on it (radius/length/width plus DistanceX/DistanceY or a Coincident
# constraint pinning the profile to its measured position) and a Pad/Pocket
# reading its Length from the feature's height. Rotated features (axis-angle
# or non-zero Euler) fall back to the old primitive path unchanged: an
# oblique boss's datum plane would need a matching AttachmentOffset rotation,
# which is fragile to get right for arbitrary measured axes -- not worth the
# risk to a feature that already renders correctly as a primitive.
# ---------------------------------------------------------------------------


_DIMENSIONED_PRIMITIVE_TYPES = {"additive_cylinder", "subtractive_cylinder", "additive_box"}


def _placement_xyz(placement: Optional[Dict[str, Any]]) -> tuple[float, float, float]:
    """Return the (x, y, z) position stored in a placement payload."""
    if not placement:
        return (0.0, 0.0, 0.0)
    position = placement.get("position") or [0.0, 0.0, 0.0]
    x = float(position[0] if len(position) > 0 else 0.0)
    y = float(position[1] if len(position) > 1 else 0.0)
    z = float(position[2] if len(position) > 2 else 0.0)
    return (x, y, z)


def _placement_has_rotation(placement: Optional[Dict[str, Any]]) -> bool:
    """Return True when a placement payload carries a non-trivial rotation."""
    if not placement:
        return False
    if placement.get("rotation_axis"):
        return True
    rotation = placement.get("rotation") or [0.0, 0.0, 0.0]
    return any(abs(float(component)) > 1e-9 for component in rotation)


def _z_token(value: float) -> str:
    """Render a Z offset as a label-safe token, e.g. 7.5 -> '7_5'."""
    return _safe_name(f"{value:.6g}")


def _emit_datum_plane(
    lines: List[str],
    body_var: str,
    body_name: str,
    feature_counter: int,
    feat_name: str,
    z: float,
) -> str:
    """Emit a named datum plane offset from the body's XY plane by *z*.

    The offset lives on the datum plane's AttachmentOffset, so it stays
    visible and editable in the tree rather than being buried in a raw
    Placement on the padded/pocketed feature.
    """
    dp_var = f"dp_{body_name}_{feature_counter}"
    dp_label = _safe_name(f"DP_{feat_name}_z{_z_token(z)}")
    lines.append(f"{dp_var} = {body_var}.newObject('PartDesign::Plane', '{dp_label}')")
    lines.append(
        f"{dp_var}.AttachmentSupport = [(_body_origin_ref({body_var}, 'XY_Plane'), '')]"
    )
    lines.append(f"{dp_var}.MapMode = 'FlatFace'")
    lines.append(
        f"{dp_var}.AttachmentOffset = FreeCAD.Placement("
        f"FreeCAD.Vector(0, 0, {z}), FreeCAD.Rotation())"
    )
    return dp_var


def _emit_circle_profile_sketch(
    lines: List[str],
    body_var: str,
    body_name: str,
    feature_counter: int,
    feat_name: str,
    radius: float,
    cx: float,
    cy: float,
    z: float,
) -> str:
    """Emit a datum plane + dimensioned circle sketch for a cylinder feature.

    Driving constraints: a Radius constraint on the circle, and either a
    Coincident-to-origin constraint (center at/near the sketch origin) or
    DistanceX/DistanceY constraints pinning the measured center -- so the
    profile stays fully constrained and editable, not just "correct by
    construction".
    """
    dp_var = _emit_datum_plane(lines, body_var, body_name, feature_counter, feat_name, z)

    sketch_var = f"sketch_{body_name}_{feature_counter}"
    sketch_label = _safe_name(f"Sketch_{feat_name}")
    lines.append(f"{sketch_var} = {body_var}.newObject('Sketcher::SketchObject', '{sketch_label}')")
    lines.append(f"{sketch_var}.AttachmentSupport = [({dp_var}, '')]")
    lines.append(f"{sketch_var}.MapMode = 'FlatFace'")
    geo_var = f"{sketch_var}_geo"
    lines.append(
        f"{geo_var} = {sketch_var}.addGeometry(Part.Circle("
        f"FreeCAD.Vector({cx}, {cy}, 0), FreeCAD.Vector(0, 0, 1), {radius}), False)"
    )
    lines.append(
        f"{sketch_var}.addConstraint(Sketcher.Constraint('Radius', {geo_var}, {radius}))"
    )
    if abs(cx) < 1e-9 and abs(cy) < 1e-9:
        lines.append(
            f"{sketch_var}.addConstraint(Sketcher.Constraint("
            f"'Coincident', {geo_var}, 3, -1, 1))"
        )
    else:
        lines.append(
            f"{sketch_var}.addConstraint(Sketcher.Constraint("
            f"'DistanceX', {geo_var}, 3, {cx}))"
        )
        lines.append(
            f"{sketch_var}.addConstraint(Sketcher.Constraint("
            f"'DistanceY', {geo_var}, 3, {cy}))"
        )
    return sketch_var


def _emit_rect_profile_sketch(
    lines: List[str],
    body_var: str,
    body_name: str,
    feature_counter: int,
    feat_name: str,
    length: float,
    width: float,
    x: float,
    y: float,
    z: float,
) -> str:
    """Emit a datum plane + dimensioned rectangle sketch for a box feature.

    The rectangle's first corner lands at the feature's (x, y) position
    (matching PartDesign::AdditiveBox's own corner-at-Placement.Base
    convention), driven by DistanceX/DistanceY constraints (or a Coincident
    to the sketch origin when that corner sits at/near (0, 0)); the two
    adjacent edges carry the driving length/width dimensions.
    """
    dp_var = _emit_datum_plane(lines, body_var, body_name, feature_counter, feat_name, z)

    sketch_var = f"sketch_{body_name}_{feature_counter}"
    sketch_label = _safe_name(f"Sketch_{feat_name}")
    lines.append(f"{sketch_var} = {body_var}.newObject('Sketcher::SketchObject', '{sketch_label}')")
    lines.append(f"{sketch_var}.AttachmentSupport = [({dp_var}, '')]")
    lines.append(f"{sketch_var}.MapMode = 'FlatFace'")

    corners = [(x, y), (x + length, y), (x + length, y + width), (x, y + width)]
    g = [f"{sketch_var}_g{i}" for i in range(4)]
    for i in range(4):
        start = corners[i]
        end = corners[(i + 1) % 4]
        lines.append(
            f"{g[i]} = {sketch_var}.addGeometry(Part.LineSegment("
            f"FreeCAD.Vector({start[0]}, {start[1]}, 0), "
            f"FreeCAD.Vector({end[0]}, {end[1]}, 0)), False)"
        )
    for i in range(4):
        j = (i + 1) % 4
        lines.append(
            f"{sketch_var}.addConstraint(Sketcher.Constraint("
            f"'Coincident', {g[i]}, 2, {g[j]}, 1))"
        )
    lines.append(f"{sketch_var}.addConstraint(Sketcher.Constraint('Horizontal', {g[0]}))")
    lines.append(f"{sketch_var}.addConstraint(Sketcher.Constraint('Horizontal', {g[2]}))")
    lines.append(f"{sketch_var}.addConstraint(Sketcher.Constraint('Vertical', {g[1]}))")
    lines.append(f"{sketch_var}.addConstraint(Sketcher.Constraint('Vertical', {g[3]}))")
    if abs(x) < 1e-9 and abs(y) < 1e-9:
        lines.append(
            f"{sketch_var}.addConstraint(Sketcher.Constraint("
            f"'Coincident', {g[0]}, 1, -1, 1))"
        )
    else:
        lines.append(
            f"{sketch_var}.addConstraint(Sketcher.Constraint("
            f"'DistanceX', {g[0]}, 1, {x}))"
        )
        lines.append(
            f"{sketch_var}.addConstraint(Sketcher.Constraint("
            f"'DistanceY', {g[0]}, 1, {y}))"
        )
    lines.append(
        f"{sketch_var}.addConstraint(Sketcher.Constraint("
        f"'DistanceX', {g[0]}, 1, {g[0]}, 2, {length}))"
    )
    lines.append(
        f"{sketch_var}.addConstraint(Sketcher.Constraint("
        f"'DistanceY', {g[1]}, 1, {g[1]}, 2, {width}))"
    )
    return sketch_var


# ---------------------------------------------------------------------------
# Designer-tree Phase 2: band-level datum planes + construction reference
# sketches for outline-stack pads, section-loft bands and bayonet channels.
#
# None of the helpers below change any solid's geometry: a datum plane
# attached to the body's XY_Plane with an AttachmentOffset reproduces the
# exact same global placement the old raw-Placement sketches used (see
# _emit_profile_sketch), and the reference sketches they carry are pure
# construction geometry with non-driving (Reference) dimensions -- inert
# w.r.t. the body's Tip/Shape, so they can be added to a body at any point,
# including after that body's Tip has already been hijacked by a doc-level
# Part::Cut/Fuse for a bayonet groove or section loft.
# ---------------------------------------------------------------------------


_BAND_OUTLINE_NAME_RE = re.compile(r"^BaseOutline(?:Band(\d+))?$")


def _band_outline_index(sketch_name: str) -> Optional[int]:
    """Return the 1-based band number for a padded_outline_stack sketch name
    ("BaseOutline" -> 1, "BaseOutlineBand2" -> 2, ...), or None if the name
    does not match that stack's naming convention.
    """
    match = _BAND_OUTLINE_NAME_RE.match(sketch_name)
    if not match:
        return None
    return 1 if match.group(1) is None else int(match.group(1))


def _polygon_reference_stats(points: List[Any]) -> tuple[float, float, float, float]:
    """Return (centroid_x, centroid_y, bbox_length, bbox_width) for a band's
    polygon vertices -- used to label a construction reference sketch.
    """
    xs = [float(p[0]) for p in points]
    ys = [float(p[1]) for p in points]
    cx = sum(xs) / len(xs)
    cy = sum(ys) / len(ys)
    length = max(xs) - min(xs)
    width = max(ys) - min(ys)
    return cx, cy, length, width


def _emit_band_datum_plane(
    lines: List[str],
    body_var: str,
    body_name: str,
    suffix: str,
    label_stem: str,
    z: float,
) -> str:
    """Emit a named datum plane offset from the body's XY plane by *z*, for
    band-level documentation (Designer-tree Phase 2). Same construction as
    the Phase 1 ``_emit_datum_plane``, but keyed by a caller-supplied
    *suffix* string instead of a shared feature counter, so multiple bands
    realised outside the per-feature loop (section lofts, bayonet channels)
    get unique, collision-free Python variable names.
    """
    dp_var = f"dp_{body_name}_{suffix}"
    dp_label = _safe_name(f"DP_{label_stem}_z{_z_token(z)}")
    lines.append(f"{dp_var} = {body_var}.newObject('PartDesign::Plane', '{dp_label}')")
    lines.append(
        f"{dp_var}.AttachmentSupport = [(_body_origin_ref({body_var}, 'XY_Plane'), '')]"
    )
    lines.append(f"{dp_var}.MapMode = 'FlatFace'")
    lines.append(
        f"{dp_var}.AttachmentOffset = FreeCAD.Placement("
        f"FreeCAD.Vector(0, 0, {z}), FreeCAD.Rotation())"
    )
    return dp_var


def _emit_band_reference_sketch(
    lines: List[str],
    body_var: str,
    body_name: str,
    suffix: str,
    label_stem: str,
    dp_var: str,
    points: List[Any],
) -> str:
    """Emit a construction-only reference sketch on a band's datum plane: a
    crosshair through the band's centroid plus non-driving DistanceX/
    DistanceY dimensions recording its bounding-box length/width. Pure
    documentation -- never consumed by a Pad/Pocket/loft.
    """
    cx, cy, length, width = _polygon_reference_stats(points)
    half_l = max(length, 1e-3) / 2.0
    half_w = max(width, 1e-3) / 2.0
    sketch_var = f"sketch_{body_name}_{suffix}_ref"
    sketch_label = _safe_name(f"Sketch_{label_stem}_ref")
    lines.append(f"{sketch_var} = {body_var}.newObject('Sketcher::SketchObject', '{sketch_label}')")
    lines.append(f"{sketch_var}.AttachmentSupport = [({dp_var}, '')]")
    lines.append(f"{sketch_var}.MapMode = 'FlatFace'")
    g0 = f"{sketch_var}_g0"
    g1 = f"{sketch_var}_g1"
    lines.append(
        f"{g0} = {sketch_var}.addGeometry(Part.LineSegment("
        f"FreeCAD.Vector({cx - half_l}, {cy}, 0), FreeCAD.Vector({cx + half_l}, {cy}, 0)), True)"
    )
    lines.append(
        f"{g1} = {sketch_var}.addGeometry(Part.LineSegment("
        f"FreeCAD.Vector({cx}, {cy - half_w}, 0), FreeCAD.Vector({cx}, {cy + half_w}, 0)), True)"
    )
    c0 = f"{sketch_var}_c0"
    c1 = f"{sketch_var}_c1"
    lines.append(
        f"{c0} = {sketch_var}.addConstraint(Sketcher.Constraint("
        f"'DistanceX', {g0}, 1, {g0}, 2, {length}))"
    )
    lines.append(f"{sketch_var}.setDriving({c0}, False)")
    lines.append(
        f"{c1} = {sketch_var}.addConstraint(Sketcher.Constraint("
        f"'DistanceY', {g1}, 1, {g1}, 2, {width}))"
    )
    lines.append(f"{sketch_var}.setDriving({c1}, False)")
    lines.append(f"{sketch_var}.Visibility = False")
    return sketch_var


def _emit_band_outline_sketch(
    lines: List[str],
    body_var: str,
    body_name: str,
    feature_counter: int,
    band_num: int,
    sketch: Dict[str, Any],
    sketch_var: str,
) -> bool:
    """Emit a padded_outline_stack band's profile sketch attached to a named
    band datum plane instead of a raw Placement offset (task a). The
    polygon geometry emitted is byte-for-byte identical to the old
    ``_emit_profile_sketch`` path -- for an XY-plane sketch (the only plane
    padded_outline_stack ever uses) a datum plane attached to the body's
    XY_Plane with AttachmentOffset (0, 0, z) resolves to exactly the same
    global placement (0, 0, z) / identity rotation that the raw Placement
    used, so the resulting Pad is the same solid; only the tree structure
    (and a new construction reference sketch recording the band's measured
    bbox) changes.
    """
    elements = sketch.get("elements", [])
    supported = [
        element
        for element in elements
        if isinstance(element, dict) and element.get("type") in {"line", "circle"}
    ]
    if not supported:
        return False

    z = float(sketch.get("offset", 0.0))
    label_stem = f"BaseOutlineStack_band{band_num}"
    suffix = f"{feature_counter}_band{band_num}"
    dp_var = _emit_band_datum_plane(lines, body_var, body_name, suffix, label_stem, z)

    outline_points = [
        element.get("start", [0.0, 0.0])
        for element in supported
        if element.get("type") == "line"
    ]
    if len(outline_points) >= 3:
        _emit_band_reference_sketch(lines, body_var, body_name, suffix, label_stem, dp_var, outline_points)

    sketch_name = _safe_name(sketch.get("name", "ProfileSketch"))
    lines.append(f"{sketch_var} = {body_var}.newObject('Sketcher::SketchObject', '{sketch_name}')")
    lines.append(f"{sketch_var}.AttachmentSupport = [({dp_var}, '')]")
    lines.append(f"{sketch_var}.MapMode = 'FlatFace'")
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


def _emit_section_loft_band_reference(
    lines: List[str],
    body_var: str,
    body_name: str,
    suffix: str,
    label_stem: str,
    sections: Any,
) -> None:
    """Emit a band datum plane + construction reference sketch documenting a
    section-loft feature's measured footprint (task a's section-loft-band
    case, and task b's doc-level fallback for bands that cannot become a
    real Pad/Pocket): the datum sits at the band's lowest recorded Z, and
    the reference sketch's crosshair/bbox dimensions cover the union of all
    of the feature's sections, not just one slice -- representative even
    for a tapering (non-constant-radius) loft. Pure documentation: the raw
    OCCT loft/cut realised alongside this call is never touched.
    """
    if not isinstance(sections, list) or not sections:
        return
    z_values = [float(section["z"]) for section in sections if isinstance(section, dict) and "z" in section]
    if not z_values:
        return
    z0 = min(z_values)
    all_points: List[Any] = []
    for section in sections:
        if isinstance(section, dict):
            all_points.extend(section.get("points") or [])
    if len(all_points) < 3:
        return
    dp_var = _emit_band_datum_plane(lines, body_var, body_name, suffix, label_stem, z0)
    _emit_band_reference_sketch(lines, body_var, body_name, suffix, label_stem, dp_var, all_points)


def _detect_constant_radius_band(
    sections: Any,
) -> Optional[tuple[float, float, float, float, float]]:
    """Return (cx, cy, radius, z0, z1) when a section-loft feature's
    sections describe a genuine constant-radius cylindrical band (every
    section shares the exact same point polygon, and that polygon's
    vertices sit at a uniform radius from their centroid) -- the case
    ring_bands / bore_bands / recess_bands loft entries always are (see
    ``_circle_loft_points`` in the pipeline's builder_plan). Returns None
    for anything else (tapering frustums, rotated rectangles, multi-contour
    stacks), which must stay a raw OCCT loft.
    """
    if not isinstance(sections, list) or len(sections) < 2:
        return None
    first_points = sections[0].get("points") if isinstance(sections[0], dict) else None
    if not isinstance(first_points, list) or len(first_points) < 8:
        return None
    for section in sections[1:]:
        if not isinstance(section, dict) or section.get("points") != first_points:
            return None
    xs = [float(p[0]) for p in first_points]
    ys = [float(p[1]) for p in first_points]
    cx = sum(xs) / len(xs)
    cy = sum(ys) / len(ys)
    radii = [math.hypot(x - cx, y - cy) for x, y in zip(xs, ys)]
    r_mean = sum(radii) / len(radii)
    if r_mean <= 1e-9:
        return None
    if max(abs(r - r_mean) for r in radii) > max(1e-3 * r_mean, 1e-6):
        return None
    try:
        z_values = [float(section["z"]) for section in sections]
    except (KeyError, TypeError, ValueError):
        return None
    z0, z1 = min(z_values), max(z_values)
    if z1 - z0 <= 1e-9:
        return None
    return (cx, cy, r_mean, z0, z1)


def _channel_angle_center(segments: Any) -> Optional[float]:
    """Return a bayonet channel's representative angular position (degrees)
    for a reference-angle dimension: the midpoint of its first
    circumferential segment's sweep, or the fixed angle of its first axial
    segment, whichever is found first.
    """
    if not isinstance(segments, list):
        return None
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        kind = seg.get("kind", "circumferential")
        if kind == "circumferential":
            a0 = seg.get("angle0")
            a1 = seg.get("angle1")
            if a0 is not None and a1 is not None:
                return (float(a0) + float(a1)) / 2.0
        elif kind == "loft":
            bottom = seg.get("bottom") or {}
            a0 = bottom.get("angle0")
            a1 = bottom.get("angle1")
            if a0 is not None and a1 is not None:
                return (float(a0) + float(a1)) / 2.0
        elif kind == "axial":
            angle = seg.get("angle")
            if angle is not None:
                return float(angle)
    return None


def _channel_z_start(segments: Any) -> Optional[float]:
    """Return a bayonet channel's starting Z (the mouth) -- the minimum z0
    across its segments (or the bottom band's z0 for a 'loft' segment)."""
    if not isinstance(segments, list):
        return None
    z_values: List[float] = []
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        if "z0" in seg:
            try:
                z_values.append(float(seg["z0"]))
            except (TypeError, ValueError):
                pass
    return min(z_values) if z_values else None


def _emit_bayonet_reference_sketch(
    lines: List[str],
    body_var: str,
    body_name: str,
    feat_name: str,
    cx: float,
    cy: float,
    r_outer: float,
    depth: float,
    channel_specs: List[Any],
    z_start: float,
    axis_var: Optional[str],
) -> None:
    """Emit a per-bayonet_groove construction sketch on a datum plane at the
    channel's z-start (task c): a baseline construction ray from the
    channel center, one construction ray per channel at its measured
    angular center (Reference 'Angle' dimension, non-driving) and a short
    construction segment recording the measured relief depth (Reference
    'DistanceX', non-driving). When the body already carries a helper axis
    through this center (see the ``first_cylinder_center`` axis in
    ``_gen_bodies``), the sketch imports it as external geometry and pins
    the baseline ray's start point to it (Coincident), anchoring the
    reference sketch to the body's main axis rather than a bare coordinate.
    Pure documentation: the bayonet's cut geometry (``_bayonet_channel_solid``)
    is untouched.
    """
    label_stem = feat_name
    suffix = f"bayonet_{_safe_name(feat_name)}"
    dp_var = _emit_band_datum_plane(lines, body_var, body_name, suffix, label_stem, z_start)

    sketch_var = f"sketch_{body_name}_{suffix}_ref"
    sketch_label = _safe_name(f"Sketch_{feat_name}_ref")
    lines.append(f"{sketch_var} = {body_var}.newObject('Sketcher::SketchObject', '{sketch_label}')")
    lines.append(f"{sketch_var}.AttachmentSupport = [({dp_var}, '')]")
    lines.append(f"{sketch_var}.MapMode = 'FlatFace'")

    if axis_var is not None:
        lines.append(f"{sketch_var}.addExternal({axis_var}.Name, '')")

    base_var = f"{sketch_var}_base"
    lines.append(
        f"{base_var} = {sketch_var}.addGeometry(Part.LineSegment("
        f"FreeCAD.Vector({cx}, {cy}, 0), FreeCAD.Vector({cx + r_outer}, {cy}, 0)), True)"
    )
    if axis_var is not None:
        lines.append(
            f"{sketch_var}.addConstraint(Sketcher.Constraint("
            f"'Coincident', {base_var}, 1, -3, 1))"
        )

    depth_var = f"{sketch_var}_depth"
    lines.append(
        f"{depth_var} = {sketch_var}.addGeometry(Part.LineSegment("
        f"FreeCAD.Vector({cx + r_outer - depth}, {cy}, 0), "
        f"FreeCAD.Vector({cx + r_outer}, {cy}, 0)), True)"
    )
    depth_c = f"{sketch_var}_depth_c"
    lines.append(
        f"{depth_c} = {sketch_var}.addConstraint(Sketcher.Constraint("
        f"'DistanceX', {depth_var}, 1, {depth_var}, 2, {depth}))"
    )
    lines.append(f"{sketch_var}.setDriving({depth_c}, False)")

    for chan_index, segments in enumerate(channel_specs, start=1):
        angle_center = _channel_angle_center(segments)
        if angle_center is None:
            continue
        angle_rad = math.radians(angle_center)
        chan_var = f"{sketch_var}_chan{chan_index}"
        end_x = cx + r_outer * math.cos(angle_rad)
        end_y = cy + r_outer * math.sin(angle_rad)
        lines.append(
            f"{chan_var} = {sketch_var}.addGeometry(Part.LineSegment("
            f"FreeCAD.Vector({cx}, {cy}, 0), FreeCAD.Vector({end_x}, {end_y}, 0)), True)"
        )
        angle_c = f"{sketch_var}_angle_c{chan_index}"
        lines.append(
            f"{angle_c} = {sketch_var}.addConstraint(Sketcher.Constraint("
            f"'Angle', {base_var}, {chan_var}, {angle_rad}))"
        )
        lines.append(f"{sketch_var}.setDriving({angle_c}, False)")

    lines.append(f"{sketch_var}.Visibility = False")


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
            "def _sector_wire(cx, cy, z, a0, a1, r_in, r_out, n=16):",
            "    # Closed polygon wire in the plane z: n+1 points on the outer arc",
            "    # r_out from a0 to a1, then n+1 points on the inner arc r_in back",
            "    # from a1 to a0. Points are absolute (built around cx, cy directly,",
            "    # no Placement applied afterwards).",
            "    import math",
            "    sweep = (a1 - a0) % 360.0",
            "    pts = []",
            "    for i in range(n + 1):",
            "        ang = math.radians(a0 + sweep * i / n)",
            "        pts.append(FreeCAD.Vector(cx + r_out * math.cos(ang), cy + r_out * math.sin(ang), z))",
            "    for i in range(n + 1):",
            "        ang = math.radians(a1 - sweep * i / n)",
            "        pts.append(FreeCAD.Vector(cx + r_in * math.cos(ang), cy + r_in * math.sin(ang), z))",
            "    return Part.makePolygon(pts + [pts[0]])",
            "",
            "def _bayonet_channel_solid(cx, cy, r_outer, depth, half_width, over, segments):",
            "    # Build one bayonet channel as the union of swept sector solids along an",
            "    # L-path wrapped on the neck cylinder. Each segment is either 'axial'",
            "    # (a box slot at a fixed angle running in z), 'circumferential' (an",
            "    # annular sector spanning an angle range at a fixed z-band), or 'loft'",
            "    # (a ruled loft between two annular-sector wires at z0/z1, giving a",
            "    # continuously tapering wedge instead of a stair-stepped sector stack).",
            "    # All parts are robust OCCT primitives (cylinders/boxes/lofted wires),",
            "    # so the swept cut never relies on wrapping a Sketcher wire onto a",
            "    # curved face.",
            "    import math",
            "    pieces = []",
            "    for seg in segments:",
            "        kind = seg.get('kind', 'circumferential')",
            "        z0 = float(seg['z0']); z1 = float(seg['z1'])",
            "        h = z1 - z0",
            "        if h <= 0:",
            "            continue",
            "        # Per-segment LOCAL ridge radius and depth: on a tapered neck the",
            "        # outer wall falls locally, so a single global r_outer leaves the",
            "        # tool floating outside the material over parts of the z range;",
            "        # likewise the measured relief depth varies along the channel",
            "        # (through-wall lead-in mouth vs shallow latch ramp). When the",
            "        # detector supplies per-segment values, anchor the groove band to",
            "        # them; otherwise fall back to the global parameters.",
            "        seg_r_outer = float(seg.get('wall_radius', r_outer))",
            "        seg_depth = float(seg.get('depth', depth))",
            "        r_in = seg_r_outer - seg_depth",
            "        r_cut_out = seg_r_outer + over",
            "        if kind == 'circumferential':",
            "            a0 = float(seg['angle0']); a1 = float(seg['angle1'])",
            "            sweep = (a1 - a0) % 360.0",
            "            if sweep <= 0:",
            "                sweep = 360.0",
            "            outer = Part.makeCylinder(r_cut_out, h, FreeCAD.Vector(cx, cy, z0), FreeCAD.Vector(0, 0, 1), sweep)",
            "            inner = Part.makeCylinder(r_in, h, FreeCAD.Vector(cx, cy, z0), FreeCAD.Vector(0, 0, 1), sweep)",
            "            sector = outer.cut(inner)",
            "            sector.Placement = FreeCAD.Placement(FreeCAD.Vector(0, 0, 0), FreeCAD.Rotation(FreeCAD.Vector(0, 0, 1), a0), FreeCAD.Vector(cx, cy, 0))",
            "            pieces.append(sector)",
            "        elif kind == 'loft':",
            "            # Ruled loft between two annular-sector wires at z0/z1: the",
            "            # angle bounds, wall radius and depth are each interpolated",
            "            # linearly between the 'bottom' and 'top' dicts, giving a",
            "            # continuously tapering wedge instead of a stack of discrete",
            "            # sector steps.",
            "            b = seg['bottom']; t = seg['top']",
            "            b_a0 = float(b['angle0']); b_a1 = float(b['angle1'])",
            "            t_a0 = float(t['angle0']); t_a1 = float(t['angle1'])",
            "            # Normalize the top angles against the bottom ones so the two",
            "            # wires agree on winding direction across the 0/360 wrap.",
            "            while t_a0 - b_a0 > 180.0:",
            "                t_a0 -= 360.0",
            "            while t_a0 - b_a0 < -180.0:",
            "                t_a0 += 360.0",
            "            while t_a1 - b_a1 > 180.0:",
            "                t_a1 -= 360.0",
            "            while t_a1 - b_a1 < -180.0:",
            "                t_a1 += 360.0",
            "            b_r_out = float(b['wall_radius']) + over",
            "            b_r_in = float(b['wall_radius']) - float(b['depth'])",
            "            t_r_out = float(t['wall_radius']) + over",
            "            t_r_in = float(t['wall_radius']) - float(t['depth'])",
            "            wire_b = _sector_wire(cx, cy, z0, b_a0, b_a1, b_r_in, b_r_out)",
            "            wire_t = _sector_wire(cx, cy, z1, t_a0, t_a1, t_r_in, t_r_out)",
            "            wedge = Part.makeLoft([wire_b, wire_t], True, True)",
            "            pieces.append(wedge)",
            "        else:  # axial slot centered at a given angle, spanning +-half_width",
            "            angle = float(seg.get('angle', 0.0))",
            "            hw = float(seg.get('half_width', half_width))",
            "            arc = math.radians(hw)",
            "            tang = 2.0 * seg_r_outer * math.sin(arc) if arc < math.pi / 2 else 2.0 * seg_r_outer",
            "            box = Part.makeBox(seg_depth + over, tang, h, FreeCAD.Vector(r_in, -tang / 2.0, z0))",
            "            box.Placement = FreeCAD.Placement(FreeCAD.Vector(cx, cy, 0), FreeCAD.Rotation(FreeCAD.Vector(0, 0, 1), angle))",
            "            pieces.append(box)",
            "    if not pieces:",
            "        return None",
            "    solid = pieces[0]",
            "    for extra in pieces[1:]:",
            "        solid = solid.fuse(extra)",
            "    return solid",
            "",
            "def _section_loft_solid(sections, ruled):",
            "    # Build a solid loft directly from absolute-coordinate polygon",
            "    # sections (e.g. planar slices of a source mesh), rather than from",
            "    # Sketcher profiles. Each section is {'z': float, 'points': [[x, y],",
            "    # ...]} describing one closed polygon at that Z height; all sections",
            "    # must share the same point count so the ruled/smooth loft pairs",
            "    # vertices index-by-index instead of guessing a correspondence.",
            "    wires = []",
            "    for section in sections:",
            "        z = section['z']",
            "        pts = [FreeCAD.Vector(p[0], p[1], z) for p in section['points']]",
            "        pts.append(pts[0])",
            "        wires.append(Part.makePolygon(pts))",
            "    return Part.makeLoft(wires, True, ruled)",
            "",
        ]
    )

    # Collect (body_var, groove_feature) so bayonet swept-cuts can be applied
    # as doc-level Part::Cut operations after each body has been recomputed.
    bayonet_cuts: List[tuple[str, str, Dict[str, Any]]] = []
    # Collect (body_var, loft_feature) so measured multi-section lofts can be
    # fused onto the body's shape doc-level after each body is recomputed
    # (raw Part.makeLoft shapes cannot be spliced into a live PartDesign tip
    # mid-tree, same constraint as the bayonet swept cuts above).
    section_lofts: List[tuple[str, str, Dict[str, Any]]] = []
    # Collect (body_var, loft_feature) for the subtractive twin of
    # additive_section_loft: same measured polygon-stack loft, but realised
    # doc-level as a Part::Cut instead of a Part::Fuse (see the emission
    # loop below for the doc-level ordering against section_lofts/bayonet).
    subtractive_section_lofts: List[tuple[str, str, Dict[str, Any]]] = []
    # additive_section_loft / subtractive_section_loft features carrying
    # after_cuts=True (e.g. a "cut-then-restore" island like a socket tube
    # rebuilt inside a cavity that is itself now cut doc-level, instead of
    # in-tree before the island used to be added): these must be realised
    # AFTER the normal fuse/cut phases below, chained onto whatever the
    # body's result is at that point -- otherwise the island would be added
    # to the not-yet-hollowed body and then erased wholesale by the cavity
    # cut that runs afterward.
    #
    # One shared list, in FEATURE ORDER, tagged "fuse"/"cut" -- NOT two
    # phase-separated lists like the normal fuse/cut phases above. The
    # deferred ops encode explicit restore chains (restore the socket tube,
    # re-drill its bore, restore the core pin standing inside that bore);
    # batching all deferred fuses before all deferred cuts would let the
    # re-drilled bore hollow back out any island fused inside its own
    # footprint (volume campaign iteration 5: the socket core pin).
    deferred_ops: List[tuple[str, str, str, Dict[str, Any]]] = []
    # Remember the first (additive/subtractive) cylinder's center per body,
    # so a helper Datum Line can be dropped on the body's main axis after its
    # features are emitted -- a reference for later manual work, not used by
    # any downstream geometry.
    first_cylinder_center: Dict[str, tuple[float, float]] = {}
    # The body's helper axis Python variable, once emitted (see below), so
    # bayonet reference sketches (task c) can anchor to it via external
    # geometry. Populated after each body's feature loop.
    body_axis_var: Dict[str, str] = {}
    # True once a body has recorded ANY doc-level op (bayonet cut or section
    # loft realised as a raw OCCT Part::Cut/Fuse) -- used to gate the
    # constant-radius-band-to-Pad/Pocket conversion below: converting is only
    # safe for the first such feature on a body, before any doc-level chain
    # exists to reorder against (task b).
    body_has_doc_level: Dict[str, bool] = {}

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

            placement = feat.get("placement") or feat_props.get("placement")

            if feat_type in _DIMENSIONED_PRIMITIVE_TYPES and not _placement_has_rotation(placement):
                x, y, z = _placement_xyz(placement)
                if feat_type in ("additive_cylinder", "subtractive_cylinder"):
                    radius = float(feat.get("radius", feat_props.get("radius", feat_props.get("Radius", 5.0))))
                    height = float(feat.get("height", feat_props.get("height", feat_props.get("Height", 10.0))))
                    if body_var not in first_cylinder_center:
                        first_cylinder_center[body_var] = (x, y)
                    sketch_var = _emit_circle_profile_sketch(
                        lines, body_var, body_name, feature_counter, feat_name, radius, x, y, z
                    )
                    op_class = "PartDesign::Pad" if feat_type == "additive_cylinder" else "PartDesign::Pocket"
                    lines.append(f"{feat_var} = {body_var}.newObject('{op_class}', '{feat_name}')")
                    lines.append(f"{feat_var}.Profile = {sketch_var}")
                    lines.append(f"{sketch_var}.Visibility = False")
                    lines.append(f"{feat_var}.Length = {height}")
                    if feat_type == "subtractive_cylinder":
                        # PartDesign::Pocket cuts opposite the sketch normal
                        # by default (-Z here); Reversed=True matches the old
                        # PartDesign::SubtractiveCylinder primitive, which
                        # (like its additive counterpart) removes material
                        # extending in +Z from its Placement.Base.
                        lines.append(f"{feat_var}.Reversed = True")
                else:  # additive_box
                    length = float(feat.get("length", feat_props.get("length", feat_props.get("Length", 10.0))))
                    width = float(feat.get("width", feat_props.get("width", feat_props.get("Width", 10.0))))
                    height = float(feat.get("height", feat_props.get("height", feat_props.get("Height", 10.0))))
                    sketch_var = _emit_rect_profile_sketch(
                        lines, body_var, body_name, feature_counter, feat_name, length, width, x, y, z
                    )
                    lines.append(f"{feat_var} = {body_var}.newObject('PartDesign::Pad', '{feat_name}')")
                    lines.append(f"{feat_var}.Profile = {sketch_var}")
                    lines.append(f"{sketch_var}.Visibility = False")
                    lines.append(f"{feat_var}.Length = {height}")
                previous_var = feat_var

            elif feat_type in primitive_map:
                class_name, *prop_pairs = primitive_map[feat_type]
                lines.append(f"{feat_var} = {body_var}.newObject('{class_name}', '{feat_name}')")
                for prop_name, key in prop_pairs:
                    value = feat.get(key, feat_props.get(key))
                    if value is not None:
                        lines.append(f"{feat_var}.{prop_name} = {float(value)}")
                placement_expr = _placement_expr(placement)
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
                    band_num = None
                    if str(sketch.get("plane", "XY")).upper() == "XY":
                        band_num = _band_outline_index(str(sketch.get("name", "")))
                    if band_num is not None:
                        emitted = _emit_band_outline_sketch(
                            lines, body_var, body_name, feature_counter, band_num, sketch, candidate_var
                        )
                    else:
                        emitted = _emit_profile_sketch(lines, body_var, sketch, candidate_var)
                    if emitted:
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
                    band_num = None
                    if str(sketch.get("plane", "XY")).upper() == "XY":
                        band_num = _band_outline_index(str(sketch.get("name", "")))
                    if band_num is not None:
                        emitted = _emit_band_outline_sketch(
                            lines, body_var, body_name, feature_counter, band_num, sketch, candidate_var
                        )
                    else:
                        emitted = _emit_profile_sketch(lines, body_var, sketch, candidate_var)
                    if emitted:
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

            elif feat_type == "bayonet_groove":
                # Swept-cut bayonet channels wrapped on the neck cylinder wall.
                # Recorded here and realised as a doc-level Part::Cut once the
                # body has been recomputed (raw Part shapes cannot be cut into
                # a live PartDesign tip mid-tree).
                bayonet_cuts.append((body_var, feat_name, feat))
                body_has_doc_level[body_var] = True

            elif feat_type in ("additive_section_loft", "subtractive_section_loft"):
                is_additive = feat_type == "additive_section_loft"
                after_cuts = bool(feat.get("after_cuts"))
                # Designer-tree Phase 2, task b: a constant-radius circular
                # band (every ring_bands/bore_bands/recess_bands loft entry
                # the pipeline emits) is geometrically just a cylinder --
                # realise it as a real dimensioned circle sketch + Pad/Pocket
                # on a named band datum plane (Phase 1 helper) instead of a
                # raw OCCT loft, wherever that is provably safe: not deferred
                # (after_cuts bands must chain onto a doc-level cut result a
                # PartDesign feature cannot reference) and not preceded by
                # another doc-level op on the same body (which would already
                # have taken over the body's Tip, making a further in-tree
                # feature build on the wrong base). Every other band --
                # tapering frustums, multi-contour stacks, deferred islands,
                # or a circular band chained after an earlier doc-level op --
                # keeps its raw-loft geometry untouched and only gets the
                # band datum-plane + reference-sketch documentation emitted
                # alongside it below (task b's doc-level fallback / task a's
                # section-loft-band documentation).
                band = None if after_cuts else _detect_constant_radius_band(feat.get("sections"))
                if band is not None and not body_has_doc_level.get(body_var, False):
                    cx, cy, radius, z0, z1 = band
                    height = z1 - z0
                    sketch_var = _emit_circle_profile_sketch(
                        lines, body_var, body_name, feature_counter, feat_name, radius, cx, cy, z0
                    )
                    op_class = "PartDesign::Pad" if is_additive else "PartDesign::Pocket"
                    lines.append(f"{feat_var} = {body_var}.newObject('{op_class}', '{feat_name}')")
                    lines.append(f"{feat_var}.Profile = {sketch_var}")
                    lines.append(f"{sketch_var}.Visibility = False")
                    lines.append(f"{feat_var}.Length = {height}")
                    if not is_additive:
                        # PartDesign::Pocket cuts opposite the sketch normal
                        # by default; Reversed=True matches the raw loft's
                        # z0->z1 (+Z) footprint (see the dimensioned
                        # subtractive_cylinder path above).
                        lines.append(f"{feat_var}.Reversed = True")
                    previous_var = feat_var
                elif is_additive:
                    # Measured multi-section point loft. Recorded here and
                    # realised as a doc-level Part::Fuse once the body has
                    # been recomputed (raw Part.makeLoft shapes cannot be
                    # spliced into a live PartDesign tip mid-tree). after_cuts
                    # defers it to run after the normal fuse/cut phases (see
                    # collection above).
                    if after_cuts:
                        deferred_ops.append(("fuse", body_var, feat_name, feat))
                    else:
                        section_lofts.append((body_var, feat_name, feat))
                    body_has_doc_level[body_var] = True
                else:
                    # Subtractive twin of additive_section_loft: the same
                    # measured polygon-stack loft, but recorded here and
                    # realised as a doc-level Part::Cut (removing material)
                    # once the body has been recomputed, chained after any
                    # additive fusion on the same body (see the emission
                    # loop below).
                    if after_cuts:
                        deferred_ops.append(("cut", body_var, feat_name, feat))
                    else:
                        subtractive_section_lofts.append((body_var, feat_name, feat))
                    body_has_doc_level[body_var] = True

            else:
                lines.append(
                    f"# WARNING: Unknown feature type '{feat_type}' "
                    f"for '{feat_name}'"
                )

            lines.append("")

        if body_var in first_cylinder_center:
            # Helper axis on the body's main axis (through the first
            # cylinder feature's center), for later manual reference -- not
            # consumed by any downstream feature or export.
            cx, cy = first_cylinder_center[body_var]
            axis_var = f"axis_{body_name}"
            axis_label = _safe_name(f"DA_{body_name}_axis")
            lines.append(f"{axis_var} = {body_var}.newObject('PartDesign::Line', '{axis_label}')")
            lines.append(
                f"{axis_var}.Placement = FreeCAD.Placement("
                f"FreeCAD.Vector({cx}, {cy}, 0), FreeCAD.Rotation())"
            )
            lines.append("")
            body_axis_var[body_var] = axis_var

    if (
        bayonet_cuts
        or section_lofts
        or subtractive_section_lofts
        or deferred_ops
    ):
        lines.append("doc.recompute()")
        lines.append("")

    # Track the current top-level result per body across these doc-level
    # ops. additive_section_loft fusions are emitted first (below), then all
    # cuts (bayonet grooves and subtractive_section_loft) chain sequentially
    # onto whatever that body's current result is -- not always the raw
    # body_var. Without this, a body carrying both an additive fusion and a
    # cut would have each op independently Base'd on the raw body, leaving
    # two disconnected top-level shapes instead of one combined final part
    # (the cut result would silently ignore the fused-in material and vice
    # versa).
    body_current: Dict[str, str] = {}

    def _current_var(body_var: str) -> str:
        return body_current.get(body_var, body_var)

    for loft_index, (body_var, feat_name, feat) in enumerate(section_lofts, start=1):
        body_name = body_var[len("body_"):]
        sections = feat.get("sections", [])
        ruled = bool(feat.get("ruled", True))
        redrill_holes = feat.get("redrill_holes") or []
        safe_feat_name = _safe_name(feat_name)

        loft_var = f"section_loft_{loft_index}"
        lines.append(f"{loft_var}_shape = _section_loft_solid({sections!r}, {ruled!r})")
        lines.append(f"{loft_var} = doc.addObject('Part::Feature', '{safe_feat_name}_loft')")
        lines.append(f"{loft_var}.Shape = {loft_var}_shape")
        lines.append(f"{loft_var}.Visibility = False")

        # the Python variable must be unique per emitted op: features of the
        # same type share a default display name, and reusing plain
        # `obj_<name>` across loop iterations rebinds the variable BEFORE
        # the next iteration's `.Base = <current>` line reads it back --
        # producing a fatal self-referencing Base ("the graph must be a
        # DAG", null downstream shapes). FreeCAD's document namespace
        # de-duplicates the display names on its own.
        fuse_var = f"obj_{safe_feat_name}_fuse{loft_index}"
        lines.append(f"{fuse_var} = doc.addObject('Part::Fuse', '{safe_feat_name}')")
        lines.append(f"{fuse_var}.Base = {_current_var(body_var)}")
        lines.append(f"{fuse_var}.Tool = {loft_var}")
        lines.append("")
        body_current[body_var] = fuse_var

        if redrill_holes:
            # A plain Part::Fuse has no notion of holes already cut into the
            # body below the loft's Z band: wherever the (hole-less) loft
            # solid overlaps a hole's XY footprint, the union silently
            # refills it. Re-cut each affected hole doc-level, after the
            # fuse, with a plain cylinder spanning its full original depth.
            redrill_var = f"redrill_{loft_index}"
            lines.append(f"{redrill_var}_pieces = []")
            for hole in redrill_holes:
                cx = float(hole["cx"])
                cy = float(hole["cy"])
                radius = float(hole["radius"])
                z0 = float(hole.get("z0", 0.0))
                z1 = float(hole["z1"])
                height = z1 - z0
                lines.append(
                    f"{redrill_var}_pieces.append(Part.makeCylinder("
                    f"{radius}, {height}, FreeCAD.Vector({cx}, {cy}, {z0}), "
                    f"FreeCAD.Vector(0, 0, 1)))"
                )
            lines.append(f"{redrill_var}_shape = {redrill_var}_pieces[0]")
            lines.append(f"for _extra in {redrill_var}_pieces[1:]:")
            lines.append(f"    {redrill_var}_shape = {redrill_var}_shape.fuse(_extra)")
            lines.append(
                f"{redrill_var}_tool = doc.addObject('Part::Feature', "
                f"'{safe_feat_name}_redrill_tool')"
            )
            lines.append(f"{redrill_var}_tool.Shape = {redrill_var}_shape")
            lines.append(f"{redrill_var}_tool.Visibility = False")
            recut_var = f"{fuse_var}_redrilled"
            lines.append(
                f"{recut_var} = doc.addObject('Part::Cut', '{safe_feat_name}_redrilled')"
            )
            lines.append(f"{recut_var}.Base = {fuse_var}")
            lines.append(f"{recut_var}.Tool = {redrill_var}_tool")
            lines.append("")
            body_current[body_var] = recut_var

        _emit_section_loft_band_reference(
            lines, body_var, body_name, f"loft{loft_index}", f"{safe_feat_name}_band{loft_index}", sections
        )
        lines.append("")

    for cut_index, (body_var, feat_name, feat) in enumerate(bayonet_cuts, start=1):
        body_name = body_var[len("body_"):]
        props = feat.get("properties", {})

        def _p(key, default):
            value = feat.get(key, props.get(key, default))
            return default if value is None else value

        cx = float(_p("center_x", 0.0))
        cy = float(_p("center_y", 0.0))
        r_outer = float(_p("wall_radius", 6.2))
        depth = float(_p("depth", 0.9))
        half_width = float(_p("half_width", 6.0))
        over = float(_p("overcut", 0.5))
        symmetry = int(_p("symmetry", 2))
        channels = feat.get("channels", props.get("channels"))
        base_segments = feat.get("segments", props.get("segments", []))

        # Exploit the measured n-fold symmetry: one channel is described, the
        # rest are angular copies at 360/symmetry spacing (unless explicit
        # per-channel segment lists are supplied).
        channel_specs: List[Any]
        if channels:
            channel_specs = list(channels)
        else:
            channel_specs = []
            step = 360.0 / max(symmetry, 1)
            for copy_index in range(max(symmetry, 1)):
                rotated = []
                for seg in base_segments:
                    seg = dict(seg)
                    offset = step * copy_index
                    if seg.get("kind", "circumferential") == "circumferential":
                        seg["angle0"] = float(seg.get("angle0", 0.0)) + offset
                        seg["angle1"] = float(seg.get("angle1", 0.0)) + offset
                    else:
                        seg["angle"] = float(seg.get("angle", 0.0)) + offset
                    rotated.append(seg)
                channel_specs.append(rotated)

        tool_var = f"bayonet_tool_{cut_index}"
        lines.append(f"{tool_var}_pieces = []")
        for chan_index, segments in enumerate(channel_specs):
            lines.append(
                f"{tool_var}_chan = _bayonet_channel_solid("
                f"{cx}, {cy}, {r_outer}, {depth}, {half_width}, {over}, {segments!r})"
            )
            lines.append(f"if {tool_var}_chan is not None:")
            lines.append(f"    {tool_var}_pieces.append({tool_var}_chan)")
        lines.append(f"if {tool_var}_pieces:")
        lines.append(f"    {tool_var}_shape = {tool_var}_pieces[0]")
        lines.append(f"    for _extra in {tool_var}_pieces[1:]:")
        lines.append(f"        {tool_var}_shape = {tool_var}_shape.fuse(_extra)")
        lines.append(f"    {tool_var} = doc.addObject('Part::Feature', '{_safe_name(feat_name)}_tool')")
        lines.append(f"    {tool_var}.Shape = {tool_var}_shape")
        lines.append(f"    {tool_var}.Visibility = False")
        # unique Python variable per op (see the section_lofts loop above)
        cut_var = f"obj_{_safe_name(feat_name)}_bayonet{cut_index}"
        lines.append(f"    {cut_var} = doc.addObject('Part::Cut', '{_safe_name(feat_name)}')")
        lines.append(f"    {cut_var}.Base = {_current_var(body_var)}")
        lines.append(f"    {cut_var}.Tool = {tool_var}")
        lines.append("")
        body_current[body_var] = cut_var

        z_starts = [z for z in (_channel_z_start(segments) for segments in channel_specs) if z is not None]
        _emit_bayonet_reference_sketch(
            lines,
            body_var,
            body_name,
            feat_name,
            cx,
            cy,
            r_outer,
            depth,
            channel_specs,
            min(z_starts) if z_starts else 0.0,
            body_axis_var.get(body_var),
        )
        lines.append("")

    for loft_index, (body_var, feat_name, feat) in enumerate(subtractive_section_lofts, start=1):
        body_name = body_var[len("body_"):]
        sections = feat.get("sections", [])
        ruled = bool(feat.get("ruled", True))
        safe_feat_name = _safe_name(feat_name)

        loft_var = f"subtractive_section_loft_{loft_index}"
        lines.append(f"{loft_var}_shape = _section_loft_solid({sections!r}, {ruled!r})")
        lines.append(f"{loft_var} = doc.addObject('Part::Feature', '{safe_feat_name}_loft')")
        lines.append(f"{loft_var}.Shape = {loft_var}_shape")
        lines.append(f"{loft_var}.Visibility = False")

        # unique Python variable per op (see the section_lofts loop above)
        cut_var = f"obj_{safe_feat_name}_cut{loft_index}"
        lines.append(f"{cut_var} = doc.addObject('Part::Cut', '{safe_feat_name}')")
        lines.append(f"{cut_var}.Base = {_current_var(body_var)}")
        lines.append(f"{cut_var}.Tool = {loft_var}")
        lines.append("")
        body_current[body_var] = cut_var

        _emit_section_loft_band_reference(
            lines, body_var, body_name, f"subloft{loft_index}", f"{safe_feat_name}_band{loft_index}", sections
        )
        lines.append("")

    # after_cuts phase: islands that must be added back (and, for a bore,
    # re-drilled) AFTER the cavity cuts above have already run -- e.g. a
    # socket tube rebuilt inside a chamber that is itself cut doc-level now,
    # instead of in-tree before the socket used to be added. Ops run in
    # FEATURE ORDER (fuses and cuts interleaved), NOT fuses-then-cuts like
    # the normal phases: the deferred ops encode explicit restore chains
    # (restore the socket tube, re-drill its bore, restore the core pin
    # standing inside that bore), and batching all fuses first would let
    # the re-drilled bore hollow back out any island fused inside its own
    # footprint (volume campaign iteration 5: the socket core pin).
    fuse_count = 0
    cut_count = 0
    for op_index, (kind, body_var, feat_name, feat) in enumerate(deferred_ops, start=1):
        body_name = body_var[len("body_"):]
        sections = feat.get("sections", [])
        ruled = bool(feat.get("ruled", True))
        safe_feat_name = _safe_name(feat_name)

        if kind == "fuse":
            fuse_count += 1
            loft_var = f"post_cut_section_loft_{fuse_count}"
            # unique Python variable per op (see the section_lofts loop above)
            op_var = f"obj_{safe_feat_name}_postfuse{fuse_count}"
            op_class = "Part::Fuse"
        else:
            cut_count += 1
            loft_var = f"post_cut_subtractive_loft_{cut_count}"
            # unique Python variable per op (see the section_lofts loop above)
            op_var = f"obj_{safe_feat_name}_postcut{cut_count}"
            op_class = "Part::Cut"

        lines.append(f"{loft_var}_shape = _section_loft_solid({sections!r}, {ruled!r})")
        lines.append(f"{loft_var} = doc.addObject('Part::Feature', '{safe_feat_name}_loft')")
        lines.append(f"{loft_var}.Shape = {loft_var}_shape")
        lines.append(f"{loft_var}.Visibility = False")
        lines.append(f"{op_var} = doc.addObject('{op_class}', '{safe_feat_name}')")
        lines.append(f"{op_var}.Base = {_current_var(body_var)}")
        lines.append(f"{op_var}.Tool = {loft_var}")
        lines.append("")
        body_current[body_var] = op_var

        _emit_section_loft_band_reference(
            lines, body_var, body_name, f"deferred{op_index}", f"{safe_feat_name}_band{op_index}", sections
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
    lines.append("# Collect the top-level shape objects for export. Objects that are")
    lines.append("# consumed by another feature (a Part::Cut/Fuse base or tool, the")
    lines.append("# features inside a PartDesign body, a hidden groove tool solid) must")
    lines.append("# NOT be exported alongside their result: meshing them too overlays")
    lines.append("# the unmodified input over the result and e.g. fills a subtractive")
    lines.append("# groove straight back in. Sketches (including the Designer-tree Phase")
    lines.append("# 2 construction-only band/bayonet reference sketches, which carry no")
    lines.append("# non-construction geometry and so have a null/invalid Shape) must never")
    lines.append("# be considered here regardless of InList -- evaluating .Shape.isValid()")
    lines.append("# on one raises an OCC exception rather than returning False.")
    lines.append("export_objects = []")
    lines.append("for obj in doc.Objects:")
    lines.append("    if obj.TypeId == 'Sketcher::SketchObject':")
    lines.append("        continue")
    lines.append("    if hasattr(obj, 'Shape') and obj.Shape.isValid() and not obj.InList:")
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
