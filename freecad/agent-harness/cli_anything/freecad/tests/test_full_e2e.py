"""
Full end-to-end tests for the cli-anything-freecad harness.

Covers three levels:
  1. TestIntermediateFiles  -- JSON project + macro generation (no FreeCAD needed)
  2. TestFreeCADBackend     -- headless FreeCAD export (skipped when not installed)
  3. TestCLISubprocess      -- subprocess invocations of the CLI entry-point
"""

from __future__ import annotations

import ast
import json
import math
import os
import struct
import subprocess
import sys
import time
from copy import deepcopy
from typing import List

import pytest
try:  # Pillow is only needed by the GUI-preview assertions below; the
    # headless backend tests must stay runnable without it.
    from PIL import Image, ImageChops
except ImportError:  # pragma: no cover
    Image = ImageChops = None

# ---------------------------------------------------------------------------
# Imports from the harness under test
# ---------------------------------------------------------------------------
from cli_anything.freecad.core.document import (
    create_document,
    open_document,
    save_document,
    get_document_info,
)
from cli_anything.freecad.core.parts import (
    add_part,
    list_parts,
    get_part,
    boolean_op,
    mirror_part,
    transform_part,
)
from cli_anything.freecad.core.sketch import (
    create_sketch,
    add_line,
    add_circle,
    add_rectangle,
    add_arc,
    add_constraint,
    close_sketch,
    list_sketches,
)
from cli_anything.freecad.core.body import (
    additive_box,
    additive_cone,
    additive_cylinder,
    additive_section_loft,
    subtractive_section_loft,
    subtractive_cylinder,
    bayonet_groove,
    create_body,
    pad,
    pocket,
    fillet,
    chamfer,
    linear_pattern,
    revolution,
    list_bodies,
    polar_pattern,
)
from cli_anything.freecad.core.materials import (
    create_material,
    assign_material,
    list_materials,
)
from cli_anything.freecad.core.export import export_project, get_export_info
from cli_anything.freecad.core import preview as preview_mod
from cli_anything.freecad.core.session import Session
from cli_anything.freecad.utils.freecad_macro_gen import generate_macro


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _has_freecad() -> bool:
    """Return True if FreeCAD headless backend can be located."""
    try:
        from cli_anything.freecad.utils.freecad_backend import find_freecad
        find_freecad()
        return True
    except (RuntimeError, Exception):
        return False


def _has_freecad_preview() -> bool:
    """Return True if a GUI-capable FreeCAD executable appears to be available."""
    try:
        from cli_anything.freecad.utils.freecad_backend import find_freecad
        path = find_freecad(gui_required=True)
        return "cmd" not in os.path.basename(path).lower()
    except (RuntimeError, Exception):
        return False


def _has_ffmpeg() -> bool:
    import shutil

    return shutil.which("ffmpeg") is not None


PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _artifact_path(manifest, artifact_id):
    for artifact in manifest["artifacts"]:
        if artifact["artifact_id"] == artifact_id:
            return os.path.join(manifest["_bundle_dir"], artifact["path"])
    raise KeyError(f"Artifact not found: {artifact_id}")


def _assert_png(path):
    assert os.path.isfile(path), f"Missing PNG artifact: {path}"
    with open(path, "rb") as fh:
        assert fh.read(8) == PNG_MAGIC, f"Invalid PNG header: {path}"
    assert os.path.getsize(path) > 0, f"Empty PNG artifact: {path}"


def _assert_png_not_blank(path):
    if Image is None:
        pytest.skip("Pillow not installed")
    _assert_png(path)
    image = Image.open(path).convert("L")
    extrema = image.getextrema()
    assert extrema != (255, 255), f"PNG artifact is fully white: {path}"


def _assert_images_differ(path_a, path_b):
    if Image is None:
        pytest.skip("Pillow not installed")
    image_a = Image.open(path_a).convert("RGB")
    image_b = Image.open(path_b).convert("RGB")
    diff = ImageChops.difference(image_a, image_b)
    assert diff.getbbox() is not None, f"Images are identical: {path_a} vs {path_b}"


def _wait_for_live_bundle_count(session_path, expected_count, timeout_s=30.0):
    deadline = time.time() + timeout_s
    latest = None
    while time.time() < deadline:
        with open(session_path, "r", encoding="utf-8") as fh:
            latest = json.load(fh)
        if latest.get("bundle_count", 0) >= expected_count:
            return latest
        time.sleep(0.5)
    raise AssertionError(f"Timed out waiting for bundle_count >= {expected_count}: {latest}")


def _resolve_cli(name: str) -> List[str]:
    """Resolve the CLI entry-point for subprocess tests.

    Prefers an installed command on PATH; falls back to ``python -m``
    unless ``CLI_ANYTHING_FORCE_INSTALLED=1`` is set.
    """
    import shutil

    force = os.environ.get("CLI_ANYTHING_FORCE_INSTALLED", "").strip() == "1"
    path = shutil.which(name)
    if path:
        print(f"[_resolve_cli] Using installed command: {path}")
        return [path]
    if force:
        raise RuntimeError(f"{name} not found in PATH. Install with: pip install -e .")
    module = (
        name.replace("cli-anything-", "cli_anything.")
        .replace("-", "_")
        + "."
        + name.split("-")[-1]
        + "_cli"
    )
    print(f"[_resolve_cli] Falling back to: {sys.executable} -m {module}")
    return [sys.executable, "-m", module]


# =========================================================================
# 1. Intermediate-file tests (no FreeCAD required)
# =========================================================================

class TestIntermediateFiles:
    """Verify project creation, manipulation, and macro generation
    using only the Python API -- no FreeCAD binary needed."""

    def test_full_project_json_structure(self, tmp_path):
        """Create a complex project and verify the JSON schema."""
        proj = create_document(name="StructureTest", units="mm")

        # Add varied parts
        add_part(proj, "box", name="MainBox", params={"length": 30, "width": 20, "height": 15})
        add_part(proj, "cylinder", name="Shaft", params={"radius": 3, "height": 50})
        add_part(proj, "sphere", name="Ball", params={"radius": 8})

        # Add a sketch with elements
        create_sketch(proj, name="BaseSketch", plane="XY")
        add_rectangle(proj, 0, corner=[0, 0], width=20, height=10)

        # Add a body with a pad
        create_body(proj, name="MainBody")
        pad(proj, 0, 0, length=15)

        # Add a material
        create_material(proj, preset="steel")

        # Save and reload
        path = str(tmp_path / "structure.json")
        save_document(proj, path)

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Top-level keys
        required_keys = {"version", "name", "units", "parts", "sketches",
                         "bodies", "materials", "metadata"}
        assert required_keys.issubset(data.keys()), (
            f"Missing keys: {required_keys - set(data.keys())}"
        )

        assert data["name"] == "StructureTest"
        assert data["units"] == "mm"
        assert data["version"] == "1.0"
        assert len(data["parts"]) == 3
        assert len(data["sketches"]) == 1
        assert len(data["bodies"]) == 1
        assert len(data["materials"]) == 1

        # Verify part structure
        box = data["parts"][0]
        assert box["type"] == "box"
        assert box["name"] == "MainBox"
        assert box["params"]["length"] == 30.0
        assert "placement" in box
        assert box["placement"]["position"] == [0.0, 0.0, 0.0]

        # Metadata
        assert "created" in data["metadata"]
        assert "modified" in data["metadata"]
        assert "software" in data["metadata"]

        print(f"\n  JSON structure validated: {path} ({os.path.getsize(path):,} bytes)")

    def test_multi_part_boolean_workflow(self):
        """Parts + booleans + materials, verify all state is consistent."""
        proj = create_document(name="BooleanTest")

        # Add base and tool
        box = add_part(proj, "box", name="Base", params={"length": 20, "width": 20, "height": 20})
        cyl = add_part(proj, "cylinder", name="Hole",
                       params={"radius": 5, "height": 30},
                       position=[10, 10, -5])

        assert len(list_parts(proj)) == 2
        assert box["id"] == 1
        assert cyl["id"] == 2

        # Boolean cut
        cut_result = boolean_op(proj, "cut", base_index=0, tool_index=1, name="CutResult")
        assert cut_result["type"] == "cut"
        assert cut_result["params"]["base_id"] == box["id"]
        assert cut_result["params"]["tool_id"] == cyl["id"]
        assert cut_result["visible"] is True

        # Source parts should now be hidden
        assert get_part(proj, 0)["visible"] is False
        assert get_part(proj, 1)["visible"] is False

        # Total parts now 3 (box, cylinder, cut-result)
        assert len(list_parts(proj)) == 3

        # Create material and assign to cut result
        mat = create_material(proj, preset="aluminum")
        assignment = assign_material(proj, material_index=0, part_index=2)
        assert assignment["material"] == mat["name"]
        assert assignment["part"] == "CutResult"

        # Verify material assignment on part
        cut_part = get_part(proj, 2)
        assert cut_part["material_index"] == 0

        # Verify material tracking
        materials = list_materials(proj)
        assert len(materials) == 1
        assert 2 in materials[0]["assigned_to"]

        print("\n  Boolean workflow verified: 2 primitives + cut + material assignment")

    def test_macro_generation_syntax(self, tmp_path):
        """Generate a macro and verify it is valid Python via ast.parse."""
        proj = create_document(name="MacroTest")
        add_part(proj, "box", name="TestBox", params={"length": 15, "width": 10, "height": 5})
        add_part(proj, "cylinder", name="TestCyl", params={"radius": 3, "height": 20})
        add_part(proj, "sphere", name="TestSphere", params={"radius": 7})

        # Create body with features
        create_sketch(proj, plane="XY")
        add_rectangle(proj, 0, corner=[0, 0], width=10, height=10)
        create_body(proj, name="ExtrudedBody")
        pad(proj, 0, 0, length=10)

        output_path = str(tmp_path / "output.step")
        macro = generate_macro(proj, output_path, export_format="step")

        # Must be non-empty
        assert len(macro) > 100, f"Macro too short: {len(macro)} chars"

        # Must be valid Python syntax
        try:
            ast.parse(macro)
        except SyntaxError as exc:
            pytest.fail(f"Generated macro has invalid Python syntax: {exc}\n\n{macro}")

        # Must contain key FreeCAD imports
        assert "import FreeCAD" in macro
        assert "import Part" in macro
        assert "doc.recompute()" in macro

        # Should reference our parts
        assert "TestBox" in macro
        assert "TestCyl" in macro
        assert "TestSphere" in macro

        # Save macro for inspection
        macro_path = str(tmp_path / "macro.py")
        with open(macro_path, "w", encoding="utf-8") as f:
            f.write(macro)

        print(f"\n  Macro: {macro_path} ({len(macro):,} chars, {macro.count(chr(10))} lines)")

    def test_macro_generation_body_primitives_and_patterns(self, tmp_path):
        """Generate a macro containing body primitive placements and pattern features."""
        proj = create_document(name="MacroBodyTower")
        create_body(proj, name="TowerBody")
        additive_box(
            proj,
            0,
            length=36,
            width=36,
            height=18,
            position=[0, 0, 0],
        )
        additive_box(
            proj,
            0,
            length=8,
            width=6,
            height=4,
            position=[22, 0, 8],
        )
        polar_pattern(proj, 0, axis="Z", angle=360, occurrences=4)
        additive_cylinder(proj, 0, radius=2.0, height=16, position=[0, 0, 18])
        linear_pattern(proj, 0, direction=[0, 0, 1], length=48, occurrences=3)
        additive_cone(proj, 0, radius1=3, radius2=0.6, height=10, position=[0, 0, 70])

        macro = generate_macro(proj, str(tmp_path / "tower.step"))
        ast.parse(macro)

        # unrotated additive_box now lowers to a dimensioned sketch on a
        # named datum plane + PartDesign::Pad (see freecad_macro_gen.py),
        # not a raw PartDesign::AdditiveBox primitive -- assert the new
        # construct plus its driving dimensions instead of the old class.
        assert "PartDesign::Plane" in macro
        assert "PartDesign::Pad" in macro
        assert "Sketcher.Constraint('DistanceX'" in macro
        assert "Sketcher.Constraint('DistanceY'" in macro
        assert "PartDesign::PolarPattern" in macro
        assert "PartDesign::LinearPattern" in macro
        assert "Placement = FreeCAD.Placement" in macro
        assert "_body_origin_ref" in macro

    def test_dimensioned_sketch_pad_pocket_constraints_in_macro(self, tmp_path):
        """additive_cylinder / subtractive_cylinder / additive_box (unrotated)
        must lower to a named datum plane + dimensioned Sketcher profile +
        Pad/Pocket -- not a bare PartDesign primitive -- with the driving
        constraints (Radius, DistanceX/DistanceY, Coincident-to-origin) a
        human would expect to find and edit later.
        """
        proj = create_document(name="DimensionedFeatures")
        create_body(proj, name="MainBody")
        # centered box: corner pinned to the sketch origin (Coincident), not
        # DistanceX/DistanceY, since it sits at (0, 0)
        additive_box(proj, 0, length=20.0, width=15.0, height=6.0)
        # off-origin cylinder boss: center pinned via DistanceX/DistanceY
        additive_cylinder(proj, 0, radius=4.0, height=9.0, position=[10.0, 8.0, 6.0])
        # off-origin subtractive bore through the boss
        subtractive_cylinder(proj, 0, radius=1.5, height=20.0, position=[10.0, 8.0, -2.0])

        macro = generate_macro(proj, str(tmp_path / "dimensioned.step"))
        ast.parse(macro)

        # named datum planes, offset from the body's XY plane
        assert "PartDesign::Plane" in macro
        assert "DP_Feature_additive_box_z_0" in macro
        assert "DP_Feature_additive_cylinder_z_6" in macro
        # driving dimensions
        assert "Sketcher.Constraint('Radius'" in macro
        assert macro.count("Sketcher.Constraint('Radius'") == 2  # additive + subtractive cylinder
        assert "Sketcher.Constraint('DistanceX'" in macro
        assert "Sketcher.Constraint('DistanceY'" in macro
        assert "Sketcher.Constraint('Coincident'" in macro
        # Pad/Pocket reference the sketch as Profile, not a raw primitive
        assert "PartDesign::Pad" in macro
        assert "PartDesign::Pocket" in macro
        assert ".Profile = sketch_" in macro
        # helper axis on the body's main axis, through the first cylinder's center
        assert "PartDesign::Line" in macro
        assert "DA_MainBody_axis" in macro
        assert "FreeCAD.Vector(10.0, 8.0, 0)" in macro

    def test_macro_generation_mirror_part_rendering(self, tmp_path):
        """Generate a macro that reconstructs mirrored primitive parts for preview/export."""
        proj = create_document(name="MirrorPreview")
        add_part(
            proj,
            "cylinder",
            name="LeftWheel",
            params={"radius": 12, "height": 6},
            position=[24, -20, 12],
            rotation=[90, 0, 0],
        )
        mirror_part(proj, 0, plane="XZ", name="RightWheel")

        macro = generate_macro(proj, str(tmp_path / "mirror.step"))
        ast.parse(macro)

        assert "obj_RightWheel = doc.addObject('Part::Cylinder', 'RightWheel')" in macro
        assert "Unknown part type 'mirror'" not in macro
        assert "obj_RightWheel.Placement = FreeCAD.Placement(FreeCAD.Vector(24.0, 20.0, 12.0)" in macro

    def test_save_load_roundtrip(self, tmp_path):
        """Save a project, reload it, verify contents are identical."""
        proj = create_document(name="RoundTrip", units="in", profile="imperial")

        add_part(proj, "box", name="BlockA", params={"length": 5, "width": 5, "height": 5})
        add_part(proj, "cone", name="ConeB",
                 params={"radius1": 3, "radius2": 1, "height": 8})

        create_sketch(proj, name="ProfileSketch", plane="XZ")
        add_line(proj, 0, start=[0, 0], end=[10, 0])
        add_circle(proj, 0, center=[5, 5], radius=3)

        create_material(proj, name="CustomMat", color=[0.5, 0.3, 0.1, 1.0],
                        metallic=0.7, roughness=0.4)
        assign_material(proj, 0, 0)

        path = str(tmp_path / "roundtrip.json")
        save_document(proj, path)

        # Reload
        loaded = open_document(path)

        # Compare key fields (metadata.modified will differ slightly, so skip it)
        assert loaded["name"] == proj["name"]
        assert loaded["units"] == proj["units"]
        assert loaded["version"] == proj["version"]
        assert len(loaded["parts"]) == len(proj["parts"])
        assert len(loaded["sketches"]) == len(proj["sketches"])
        assert len(loaded["bodies"]) == len(proj["bodies"])
        assert len(loaded["materials"]) == len(proj["materials"])

        # Deep-compare parts
        for i, (orig, reloaded) in enumerate(zip(proj["parts"], loaded["parts"])):
            assert orig["name"] == reloaded["name"], f"Part {i} name mismatch"
            assert orig["type"] == reloaded["type"], f"Part {i} type mismatch"
            assert orig["params"] == reloaded["params"], f"Part {i} params mismatch"

        # Deep-compare sketches
        for i, (orig, reloaded) in enumerate(zip(proj["sketches"], loaded["sketches"])):
            assert orig["name"] == reloaded["name"], f"Sketch {i} name mismatch"
            assert orig["plane"] == reloaded["plane"], f"Sketch {i} plane mismatch"
            assert len(orig["elements"]) == len(reloaded["elements"])

        print(f"\n  Round-trip verified: {path} ({os.path.getsize(path):,} bytes)")

    def test_complex_workflow(self, tmp_path):
        """Full pipeline: document -> parts -> sketch -> body -> materials."""
        # 1. Create document
        proj = create_document(name="ComplexWorkflow", profile="print3d")
        assert proj["units"] == "mm"

        # 2. Add multiple parts
        box = add_part(proj, "box", name="Platform",
                       params={"length": 50, "width": 50, "height": 5})
        cyl = add_part(proj, "cylinder", name="Pillar",
                       params={"radius": 5, "height": 40},
                       position=[25, 25, 5])
        sphere = add_part(proj, "sphere", name="Top",
                          params={"radius": 8},
                          position=[25, 25, 45])

        # 3. Transform a part
        transform_part(proj, 2, position=[25, 25, 50], rotation=[0, 0, 45])
        top = get_part(proj, 2)
        assert top["placement"]["position"] == [25.0, 25.0, 50.0]
        assert top["placement"]["rotation"] == [0.0, 0.0, 45.0]

        # 4. Boolean fuse
        fuse_result = boolean_op(proj, "fuse", 0, 1, name="PlatformPillar")
        assert fuse_result["type"] == "fuse"
        assert len(list_parts(proj)) == 4  # box, cyl, sphere, fuse

        # 5. Create sketch with various elements
        sk = create_sketch(proj, name="DetailSketch", plane="XY", offset=5.0)
        assert sk["plane"] == "XY"
        assert sk["offset"] == 5.0

        add_rectangle(proj, 0, corner=[10, 10], width=30, height=30)
        add_circle(proj, 0, center=[25, 25], radius=10)
        add_arc(proj, 0, center=[25, 25], radius=15, start_angle=0, end_angle=180)

        # Add a constraint
        sketch_data = proj["sketches"][0]
        line_ids = [el["id"] for el in sketch_data["elements"] if el["type"] == "line"]
        assert len(line_ids) >= 2, "Should have at least 2 lines from rectangle"
        add_constraint(proj, 0, "horizontal", [line_ids[0]])

        # Close the sketch
        closed = close_sketch(proj, 0)
        assert closed["closed"] is True

        sketches = list_sketches(proj)
        assert len(sketches) == 1
        assert sketches[0]["closed"] is True
        assert sketches[0]["element_count"] >= 6  # 4 rect lines + circle + arc

        # 6. Create body with features
        body = create_body(proj, name="DetailBody")
        # Create a new open sketch for the body
        create_sketch(proj, name="BodySketch", plane="XY")
        add_rectangle(proj, 1, corner=[0, 0], width=20, height=20)

        pad_feat = pad(proj, 0, 1, length=20)
        assert pad_feat["type"] == "pad"
        assert pad_feat["length"] == 20.0

        fillet_feat = fillet(proj, 0, radius=2.0)
        assert fillet_feat["type"] == "fillet"
        assert fillet_feat["radius"] == 2.0

        bodies = list_bodies(proj)
        assert len(bodies) == 1
        assert bodies[0]["feature_count"] == 2

        # 7. Materials
        steel = create_material(proj, preset="steel")
        copper = create_material(proj, preset="copper")
        assert steel["preset"] == "steel"
        assert copper["preset"] == "copper"

        assign_material(proj, 0, 0)  # steel -> Platform(box)
        assign_material(proj, 1, 1)  # copper -> Pillar(cylinder)

        mats = list_materials(proj)
        assert len(mats) == 2
        assert 0 in mats[0]["assigned_to"]
        assert 1 in mats[1]["assigned_to"]

        # 8. Save and verify
        path = str(tmp_path / "complex.json")
        saved = save_document(proj, path)
        assert os.path.isfile(saved)

        info = get_document_info(proj)
        assert info["parts_count"] == 4
        assert info["sketches_count"] == 2
        assert info["bodies_count"] == 1
        assert info["materials_count"] == 2

        # 9. Generate macro
        macro = generate_macro(proj, str(tmp_path / "complex.step"))
        ast.parse(macro)  # valid Python

        # 10. Export info
        exp_info = get_export_info(proj)
        assert exp_info["part_count"] == 4
        assert "Platform" in exp_info["part_names"]

        print(f"\n  Complex workflow: {path} ({os.path.getsize(path):,} bytes)")
        print(f"  Parts: {info['parts_count']}, Sketches: {info['sketches_count']}, "
              f"Bodies: {info['bodies_count']}, Materials: {info['materials_count']}")


# =========================================================================
# 2. FreeCAD backend tests (require FreeCAD installed)
# =========================================================================

def _stl_mesh_volume(path: str) -> float:
    """Signed volume (mm^3) of a closed STL mesh via the divergence theorem."""

    def tet(v0, v1, v2) -> float:
        return (
            v0[0] * (v1[1] * v2[2] - v1[2] * v2[1])
            - v0[1] * (v1[0] * v2[2] - v1[2] * v2[0])
            + v0[2] * (v1[0] * v2[1] - v1[1] * v2[0])
        ) / 6.0

    with open(path, "rb") as f:
        head = f.read(80)
    volume = 0.0
    if head.decode("ascii", errors="ignore").strip().lower().startswith("solid"):
        verts: List[tuple] = []
        with open(path, "r", encoding="ascii", errors="ignore") as f:
            for line in f:
                parts = line.split()
                if parts[:1] == ["vertex"]:
                    verts.append(tuple(float(p) for p in parts[1:4]))
        for i in range(0, len(verts) - 2, 3):
            volume += tet(verts[i], verts[i + 1], verts[i + 2])
    else:
        record = struct.Struct("<12fH")
        with open(path, "rb") as f:
            f.seek(80)
            (count,) = struct.unpack("<I", f.read(4))
            data = f.read(count * record.size)
        for i in range(count):
            vals = record.unpack_from(data, i * record.size)
            volume += tet(vals[3:6], vals[6:9], vals[9:12])
    return abs(volume)


def _stl_mesh_bbox(path: str) -> tuple:
    """Axis-aligned bounding box (xmin, xmax, ymin, ymax, zmin, zmax) of an
    STL mesh, parsed the same way as :func:`_stl_mesh_volume`."""
    with open(path, "rb") as f:
        head = f.read(80)
    verts: List[tuple] = []
    if head.decode("ascii", errors="ignore").strip().lower().startswith("solid"):
        with open(path, "r", encoding="ascii", errors="ignore") as f:
            for line in f:
                parts = line.split()
                if parts[:1] == ["vertex"]:
                    verts.append(tuple(float(p) for p in parts[1:4]))
    else:
        record = struct.Struct("<12fH")
        with open(path, "rb") as f:
            f.seek(80)
            (count,) = struct.unpack("<I", f.read(4))
            data = f.read(count * record.size)
        for i in range(count):
            vals = record.unpack_from(data, i * record.size)
            verts.append(vals[3:6])
            verts.append(vals[6:9])
            verts.append(vals[9:12])
    xs = [v[0] for v in verts]
    ys = [v[1] for v in verts]
    zs = [v[2] for v in verts]
    return (min(xs), max(xs), min(ys), max(ys), min(zs), max(zs))


@pytest.mark.skipif(not _has_freecad(), reason="FreeCAD not installed")
class TestFreeCADBackend:
    """Tests that require the real FreeCAD headless backend."""

    def test_find_freecad(self):
        """Verify that find_freecad returns a valid path."""
        from cli_anything.freecad.utils.freecad_backend import find_freecad

        path = find_freecad()
        assert os.path.isfile(path), f"FreeCAD not found at: {path}"
        print(f"\n  FreeCAD found: {path}")

    def test_get_version(self):
        """Verify that get_version returns a version string."""
        from cli_anything.freecad.utils.freecad_backend import get_version

        version = get_version()
        assert isinstance(version, str)
        assert len(version) > 0
        # Should contain at least one digit and a dot
        assert any(c.isdigit() for c in version), f"No digits in version: {version}"
        print(f"\n  FreeCAD version: {version}")

    def test_export_box_step(self, tmp_path):
        """Create a project with a box, export to STEP, validate format."""
        proj = create_document(name="StepExport")
        add_part(proj, "box", name="ExportBox",
                 params={"length": 20, "width": 15, "height": 10})

        output = str(tmp_path / "box.step")
        result = export_project(proj, output, preset="step")

        assert os.path.isfile(output)
        size = os.path.getsize(output)
        assert size > 0, "STEP file is empty"

        # Validate STEP header
        with open(output, "r", encoding="utf-8", errors="ignore") as f:
            header = f.read(64)
        assert header.strip().startswith("ISO-10303-21"), (
            f"Invalid STEP header: {header[:40]!r}"
        )

        print(f"\n  STEP: {output} ({size:,} bytes)")

    def test_export_multi_part_stl(self, tmp_path):
        """Export multiple parts to STL, validate format."""
        proj = create_document(name="StlExport")
        add_part(proj, "box", name="Block",
                 params={"length": 10, "width": 10, "height": 10})
        add_part(proj, "cylinder", name="Rod",
                 params={"radius": 3, "height": 20},
                 position=[15, 0, 0])

        output = str(tmp_path / "multi.stl")
        result = export_project(proj, output, preset="stl")

        assert os.path.isfile(output)
        size = os.path.getsize(output)
        assert size > 0, "STL file is empty"

        # Validate STL: ASCII starts with "solid", binary has 80-byte header
        with open(output, "rb") as f:
            head = f.read(80)

        text_head = head.decode("ascii", errors="ignore").strip().lower()
        is_ascii = text_head.startswith("solid")

        is_binary = False
        if not is_ascii:
            with open(output, "rb") as f:
                f.seek(80)
                count_bytes = f.read(4)
                if len(count_bytes) == 4:
                    tri_count = struct.unpack("<I", count_bytes)[0]
                    is_binary = tri_count > 0

        assert is_ascii or is_binary, "File is neither ASCII nor binary STL"

        fmt = "ASCII" if is_ascii else "binary"
        print(f"\n  STL ({fmt}): {output} ({size:,} bytes)")

    def test_export_fcstd(self, tmp_path):
        """Export to native FCStd format."""
        proj = create_document(name="FcstdExport")
        add_part(proj, "box", name="NativeBox",
                 params={"length": 25, "width": 25, "height": 25})

        output = str(tmp_path / "native.FCStd")
        result = export_project(proj, output, preset="fcstd")

        assert os.path.isfile(output)
        size = os.path.getsize(output)
        assert size > 0, "FCStd file is empty"

        print(f"\n  FCStd: {output} ({size:,} bytes)")

    def test_dimensioned_primitives_volume_and_bbox_regression(self, tmp_path):
        """Geometry regression for the sketch+pad/pocket rewrite of
        additive_cylinder / subtractive_cylinder / additive_box: an
        off-origin box with a boss cylinder on top and a through-bore must
        still export the exact analytic volume and bounding box (within
        +-0.1%) that the old bare PartDesign primitives produced -- the
        dimensioned-sketch path must be geometrically transparent, not just
        "close enough".
        """
        proj = create_document(name="DimensionedRegression")
        create_body(proj)
        # off-origin box: corner at (3, 2, 0), spans to (23, 17, 6)
        additive_box(proj, 0, length=20.0, width=15.0, height=6.0, position=[3.0, 2.0, 0.0])
        # boss cylinder centered inside the box footprint, sitting on top
        additive_cylinder(proj, 0, radius=4.0, height=9.0, position=[10.0, 8.0, 6.0])
        # through-bore, same axis, taller than the stack so it pierces cleanly
        subtractive_cylinder(proj, 0, radius=1.5, height=20.0, position=[10.0, 8.0, -2.0])

        output = str(tmp_path / "dimensioned.stl")
        export_project(proj, output, preset="stl")

        box_volume = 20.0 * 15.0 * 6.0
        boss_volume = math.pi * 4.0 ** 2 * 9.0
        # the bore only removes material where it overlaps the solid stack
        # (box bottom z=0 to boss top z=15), not its full nominal length
        bore_volume = math.pi * 1.5 ** 2 * 15.0
        expected_volume = box_volume + boss_volume - bore_volume

        volume = _stl_mesh_volume(output)
        assert abs(volume - expected_volume) < 0.001 * expected_volume, (
            f"expected ~{expected_volume:.3f} mm^3, got {volume:.3f} mm^3"
        )

        xmin, xmax, ymin, ymax, zmin, zmax = _stl_mesh_bbox(output)
        expected_bbox = (3.0, 23.0, 2.0, 17.0, 0.0, 15.0)
        got_bbox = (xmin, xmax, ymin, ymax, zmin, zmax)
        for got, expected in zip(got_bbox, expected_bbox):
            tol = max(0.001 * abs(expected), 1e-6)
            assert abs(got - expected) < tol, (
                f"bbox mismatch: expected {expected_bbox}, got {got_bbox}"
            )

        print(f"\n  dimensioned regression volume: {volume:.3f} mm^3 "
              f"(expected {expected_volume:.3f}), bbox: {got_bbox}")

    def test_fcstd_datum_plane_sketch_pad_structure(self, tmp_path):
        """Open the exported FCStd of a small dimensioned-feature project and
        assert, at the real FreeCAD-object level (not just macro text), that
        the datum plane exists, its sketch carries constraints, and the Pad
        references that sketch as its Profile.
        """
        from cli_anything.freecad.utils.freecad_backend import run_macro_content

        proj = create_document(name="DimensionedStructure")
        create_body(proj, name="MainBody")
        additive_cylinder(proj, 0, radius=6.2, height=7.5, position=[7.2, 7.2, 0.0])

        output = str(tmp_path / "structure.FCStd")
        export_project(proj, output, preset="fcstd")

        inspect_script = f"""
import FreeCAD
import json

doc = FreeCAD.openDocument({output!r})
body = doc.getObject('MainBody')
planes = [o for o in doc.Objects if o.TypeId == 'PartDesign::Plane']
sketches = [o for o in doc.Objects if o.TypeId == 'Sketcher::SketchObject']
pads = [o for o in doc.Objects if o.TypeId == 'PartDesign::Pad']
axes = [o for o in doc.Objects if o.TypeId == 'PartDesign::Line']

result = {{
    "plane_labels": [o.Label for o in planes],
    "sketch_constraint_counts": [o.ConstraintCount for o in sketches],
    "pad_profile_is_sketch": [
        p.Profile[0].TypeId == 'Sketcher::SketchObject'
        if p.Profile and isinstance(p.Profile, tuple) else False
        for p in pads
    ],
    "axis_labels": [o.Label for o in axes],
}}
print("RESULT_JSON:" + json.dumps(result))
"""
        run_result = run_macro_content(inspect_script, timeout=60)
        assert run_result["returncode"] == 0, run_result["stderr"]

        json_line = next(
            line for line in run_result["stdout"].splitlines() if line.startswith("RESULT_JSON:")
        )
        result = json.loads(json_line[len("RESULT_JSON:"):])

        assert len(result["plane_labels"]) == 1
        assert result["plane_labels"][0].startswith("DP_")
        assert len(result["sketch_constraint_counts"]) == 1
        assert result["sketch_constraint_counts"][0] > 0
        assert len(result["pad_profile_is_sketch"]) == 1
        assert result["pad_profile_is_sketch"][0] is True
        assert len(result["axis_labels"]) == 1
        assert result["axis_labels"][0] == "DA_MainBody_axis"

        print(f"\n  FCStd structure: plane={result['plane_labels']}, "
              f"sketch constraints={result['sketch_constraint_counts']}, "
              f"axis={result['axis_labels']}")

    def test_bayonet_cut_bites_narrowed_neck_via_segment_wall_radius(self, tmp_path):
        """Regression for the FreeCAD-STL-Importer s15-full case: on a tapered
        neck the detector's global wall_radius (6.26) over-reports the local
        ridge (5.3), so the groove band ``[wall-depth, wall+over]`` floated
        entirely outside the material and the swept cut removed 0 mm^3.
        Per-segment ``wall_radius`` values must anchor the tool to the local
        wall so the cut bites real volume; without them the old behavior
        (inert cut) is reproduced.
        """

        def neck_project(name: str, groove: str) -> dict:
            proj = create_document(name=name)
            create_body(proj)
            # narrowed (tapered-down) neck section: true local ridge r=5.3
            additive_cylinder(proj, body_index=0, radius=5.3, height=7.5,
                              position=[0, 0, 8.5])
            if groove == "none":
                return proj
            segments = [
                {"kind": "axial", "angle": 130.0, "half_width": 12.0,
                 "z0": 9.0, "z1": 11.5},
                {"kind": "circumferential", "angle0": 130.0, "angle1": 164.0,
                 "z0": 11.5, "z1": 12.5},
                {"kind": "axial", "angle": 164.0, "half_width": 6.0,
                 "z0": 12.0, "z1": 14.5},
            ]
            if groove == "local":
                for seg in segments:
                    seg["wall_radius"] = 5.3
            # global wall_radius deliberately over-reports the local ridge
            bayonet_groove(proj, body_index=0, segments=segments,
                           wall_radius=6.26, depth=0.9,
                           center_x=0.0, center_y=0.0, symmetry=2)
            return proj

        volumes = {}
        for label in ("none", "global", "local"):
            proj = neck_project(f"BayonetTaper_{label}", label)
            output = str(tmp_path / f"neck_{label}.stl")
            export_project(proj, output, preset="stl")
            volumes[label] = _stl_mesh_volume(output)

        # sanity: the export contains exactly the cut result, not the uncut
        # body or the groove tool overlaid on top of it
        intact = math.pi * 5.3**2 * 7.5
        assert abs(volumes["none"] - intact) < 0.05 * intact, (
            f"ungrooved neck export should be one clean cylinder shell "
            f"(~{intact:.0f} mm^3), got {volumes['none']:.1f} mm^3"
        )
        # old behavior (global radius only): the tool floats outside the
        # narrowed wall and removes essentially nothing
        inert = volumes["none"] - volumes["global"]
        assert abs(inert) < 1.0, (
            f"expected an inert cut without per-segment radii, but it removed "
            f"{inert:.2f} mm^3 (volumes: {volumes})"
        )
        # per-segment radii: the groove bites clearly into the wall
        removed = volumes["none"] - volumes["local"]
        assert removed > 5.0, (
            f"per-segment wall_radius cut only {removed:.2f} mm^3 "
            f"(volumes: {volumes})"
        )
        print(f"\n  bayonet bite: {removed:.1f} mm^3 "
              f"(none={volumes['none']:.1f}, global={volumes['global']:.1f}, "
              f"local={volumes['local']:.1f})")

    def test_circumferential_sector_cuts_off_origin_neck(self, tmp_path):
        """Regression for the FreeCAD-STL-Importer s18 case: the sector tool's
        placement rotated the sector about (cx, cy) but then translated it by
        (cx, cy) again, doubling the offset. Any neck whose axis sits away
        from the document origin lost its circumferential groove cut almost
        entirely; a neck centered at the origin never exposed the bug.
        """

        def neck_project(name: str, with_groove: bool) -> dict:
            proj = create_document(name=name)
            create_body(proj)
            additive_cylinder(proj, body_index=0, radius=6.236, height=7.87,
                              position=[7.2, 7.15, 8.17])
            if with_groove:
                segments = [
                    {"kind": "circumferential", "angle0": 69.0, "angle1": 157.0,
                     "z0": 8.9, "z1": 10.4, "wall_radius": 6.23, "depth": 0.95},
                ]
                bayonet_groove(proj, body_index=0, segments=segments,
                               wall_radius=6.256, depth=0.9,
                               center_x=7.2, center_y=7.15, symmetry=1)
            return proj

        volumes = {}
        for label, with_groove in (("none", False), ("groove", True)):
            proj = neck_project(f"OffOriginNeck_{label}", with_groove)
            output = str(tmp_path / f"neck_{label}.stl")
            export_project(proj, output, preset="stl")
            volumes[label] = _stl_mesh_volume(output)

        # analytical expectation: an 88 deg ring sector between the local
        # wall (5.28) and the segment's wall_radius (6.23), 1.5 mm tall,
        # plus a small overcut sliver out to 6.236 -> ~10 mm^3
        removed = volumes["none"] - volumes["groove"]
        assert 8.0 < removed < 14.0, (
            f"expected the circumferential sector to remove ~10 mm^3 from "
            f"the off-origin neck, got {removed:.2f} mm^3 "
            f"(volumes: {volumes})"
        )
        print(f"\n  off-origin sector bite: {removed:.2f} mm^3 "
              f"(none={volumes['none']:.1f}, groove={volumes['groove']:.1f})")

    def test_loft_segment_cuts_tapering_wedge_off_origin(self, tmp_path):
        """Regression for the FreeCAD-STL-Importer s20 case: the scan shows no
        staircase steps in the bayonet groove, so a 'loft' segment must cut a
        continuously tapering wedge between two measured bands (rather than
        the discrete per-band sector stack from s18/s19). The angular sweep
        narrows from 88 deg at z=9.0 to 32 deg at z=12.0 while the wall/depth
        stay fixed, so the removed volume should sit near a ring sector
        integrated over a linearly-shrinking sweep.
        """

        def neck_project(name: str, with_groove: bool) -> dict:
            proj = create_document(name=name)
            create_body(proj)
            additive_cylinder(proj, body_index=0, radius=6.236, height=7.87,
                              position=[7.2, 7.15, 8.17])
            if with_groove:
                segments = [
                    {
                        "kind": "loft", "z0": 9.0, "z1": 12.0,
                        "bottom": {"angle0": 69.0, "angle1": 157.0,
                                   "wall_radius": 6.23, "depth": 0.95},
                        "top": {"angle0": 119.0, "angle1": 151.0,
                                "wall_radius": 6.23, "depth": 0.95},
                    },
                ]
                bayonet_groove(proj, body_index=0, segments=segments,
                               wall_radius=6.256, depth=0.9,
                               center_x=7.2, center_y=7.15, symmetry=1)
            return proj

        volumes = {}
        for label, with_groove in (("none", False), ("wedge", True)):
            proj = neck_project(f"LoftWedgeNeck_{label}", with_groove)
            output = str(tmp_path / f"neck_{label}.stl")
            export_project(proj, output, preset="stl")
            volumes[label] = _stl_mesh_volume(output)

        # analytical expectation: sweep narrows linearly from 88 deg (z=9.0)
        # to 32 deg (z=12.0), mean sweep ~60 deg, ring 5.28..6.236 in the
        # material, 3.0 mm tall -> (60/360) * pi * (6.236^2 - 5.28^2) * 3.0
        # ~= 17.3 mm^3, plus a small overcut sliver.
        removed = volumes["none"] - volumes["wedge"]
        assert 14.0 < removed < 22.0, (
            f"expected the loft wedge to remove ~17.3 mm^3 from the "
            f"off-origin neck, got {removed:.2f} mm^3 (volumes: {volumes})"
        )
        print(f"\n  loft wedge bite: {removed:.2f} mm^3 "
              f"(none={volumes['none']:.1f}, wedge={volumes['wedge']:.1f})")

    def _square_loft_points(self, cx, cy, half):
        """12-point outline of an exact square (3 evenly-spaced points per
        side) centered at (cx, cy), used so a ruled loft between scaled
        copies produces an analytically checkable frustum-stack volume.
        """
        corners = [(-half, -half), (half, -half), (half, half), (-half, half)]
        pts = []
        for i in range(4):
            x0, y0 = corners[i]
            x1, y1 = corners[(i + 1) % 4]
            for t in (0.0, 1.0 / 3.0, 2.0 / 3.0):
                pts.append([cx + x0 + (x1 - x0) * t, cy + y0 + (y1 - y0) * t])
        return pts

    def test_additive_section_loft_matches_prismatic_average_off_origin(self, tmp_path):
        """Regression for the FreeCAD-STL-Importer Issue #22 lid loft: a
        generic additive multi-section loft, built from raw polygon
        cross-sections (not Sketcher profiles) and fused doc-level onto a
        body, must produce the expected solid even when its center sits away
        from the document origin. A 3-level loft (10x10 -> 8x8 -> 10x10
        squares) is a stack of two square frustums; for a ruled loft between
        linearly-scaled, correspondence-aligned square outlines, the volume
        of each segment is the exact frustum integral
        ``h * (s0**2 + s0*s1 + s1**2) / 3``.
        """
        cx, cy = 7.0, 5.0

        def build(name: str, with_loft: bool) -> dict:
            proj = create_document(name=name)
            create_body(proj)
            additive_box(proj, body_index=0, length=10.0, width=10.0, height=2.0,
                        position=[cx - 5.0, cy - 5.0, 0.0])
            if with_loft:
                sections = [
                    {"z": 2.0, "points": self._square_loft_points(cx, cy, 5.0)},
                    {"z": 4.0, "points": self._square_loft_points(cx, cy, 4.0)},
                    {"z": 6.0, "points": self._square_loft_points(cx, cy, 5.0)},
                ]
                additive_section_loft(proj, body_index=0, sections=sections, ruled=True)
            return proj

        volumes = {}
        for label, with_loft in (("box_only", False), ("with_loft", True)):
            proj = build(f"SectionLoft_{label}", with_loft)
            output = str(tmp_path / f"loft_{label}.stl")
            export_project(proj, output, preset="stl")
            volumes[label] = _stl_mesh_volume(output)

        box_volume = 10.0 * 10.0 * 2.0
        assert abs(volumes["box_only"] - box_volume) < 0.05 * box_volume, (
            f"sanity check: base box alone should be ~{box_volume:.1f} mm^3, "
            f"got {volumes['box_only']:.1f} mm^3"
        )

        seg1 = 2.0 * (10.0 ** 2 + 10.0 * 8.0 + 8.0 ** 2) / 3.0
        seg2 = 2.0 * (8.0 ** 2 + 8.0 * 10.0 + 10.0 ** 2) / 3.0
        expected_total = box_volume + seg1 + seg2

        assert abs(volumes["with_loft"] - expected_total) < 0.05 * expected_total, (
            f"expected ~{expected_total:.1f} mm^3 (box {box_volume:.1f} + "
            f"loft {seg1 + seg2:.1f}), got {volumes['with_loft']:.1f} mm^3"
        )
        print(f"\n  section loft volume: {volumes['with_loft']:.2f} mm^3 "
              f"(expected ~{expected_total:.2f} mm^3, box-only={volumes['box_only']:.2f})")

    def test_additive_section_loft_rejects_mismatched_point_counts(self):
        """A section loft with sections that don't all share the same point
        count must fail fast with a ValueError rather than silently building
        a twisted or self-intersecting OCCT loft.
        """
        proj = create_document(name="LoftValidation")
        create_body(proj)
        additive_box(proj, body_index=0, length=10.0, width=10.0, height=2.0)

        good = self._square_loft_points(0.0, 0.0, 5.0)
        short = good[:-1]
        with pytest.raises(ValueError):
            additive_section_loft(
                proj, body_index=0,
                sections=[
                    {"z": 2.0, "points": good},
                    {"z": 4.0, "points": short},
                    {"z": 6.0, "points": good},
                ],
            )
        # fewer than 3 sections is also rejected
        with pytest.raises(ValueError):
            additive_section_loft(
                proj, body_index=0,
                sections=[{"z": 2.0, "points": good}, {"z": 4.0, "points": good}],
            )

    def test_subtractive_section_loft_matches_frustum_stack_off_origin(self, tmp_path):
        """Regression for the FreeCAD-STL-Importer volume campaign iteration
        3 (cutting the adapter's main cavity from measured inner-contour
        bands): the subtractive twin of additive_section_loft must remove
        the expected frustum-stack volume from a block, as a doc-level
        Part::Cut, even off-origin. A 10x10x10 block is cut with a 3-level
        loft (10x10 -> 8x8 -> 10x10 squares spanning z=2..6), i.e. the same
        two square frustums as the additive test above; the removed volume
        is the block volume minus the cut result's volume.
        """
        cx, cy = 7.0, 5.0

        def build(name: str, with_cut: bool) -> dict:
            proj = create_document(name=name)
            create_body(proj)
            additive_box(proj, body_index=0, length=10.0, width=10.0, height=10.0,
                        position=[cx - 5.0, cy - 5.0, 0.0])
            if with_cut:
                sections = [
                    {"z": 2.0, "points": self._square_loft_points(cx, cy, 5.0)},
                    {"z": 4.0, "points": self._square_loft_points(cx, cy, 4.0)},
                    {"z": 6.0, "points": self._square_loft_points(cx, cy, 5.0)},
                ]
                subtractive_section_loft(proj, body_index=0, sections=sections, ruled=True)
            return proj

        volumes = {}
        for label, with_cut in (("block_only", False), ("with_cut", True)):
            proj = build(f"SubSectionLoft_{label}", with_cut)
            output = str(tmp_path / f"subloft_{label}.stl")
            export_project(proj, output, preset="stl")
            volumes[label] = _stl_mesh_volume(output)

        block_volume = 10.0 * 10.0 * 10.0
        assert abs(volumes["block_only"] - block_volume) < 0.05 * block_volume, (
            f"sanity check: block alone should be ~{block_volume:.1f} mm^3, "
            f"got {volumes['block_only']:.1f} mm^3"
        )

        seg1 = 2.0 * (10.0 ** 2 + 10.0 * 8.0 + 8.0 ** 2) / 3.0
        seg2 = 2.0 * (8.0 ** 2 + 8.0 * 10.0 + 10.0 ** 2) / 3.0
        expected_removed = seg1 + seg2
        removed = volumes["block_only"] - volumes["with_cut"]

        assert abs(removed - expected_removed) < 0.15 * expected_removed, (
            f"expected ~{expected_removed:.1f} mm^3 removed (frustum stack), "
            f"got {removed:.1f} mm^3 (block={volumes['block_only']:.1f}, "
            f"cut={volumes['with_cut']:.1f})"
        )
        print(f"\n  subtractive section loft removed volume: {removed:.2f} mm^3 "
              f"(expected ~{expected_removed:.2f} mm^3)")

    def test_subtractive_section_loft_rejects_mismatched_point_counts(self):
        """Same fail-fast contract as additive_section_loft: sections that
        don't all share the same point count must raise ValueError rather
        than silently building a twisted or self-intersecting OCCT loft."""
        proj = create_document(name="SubLoftValidation")
        create_body(proj)
        additive_box(proj, body_index=0, length=10.0, width=10.0, height=10.0)

        good = self._square_loft_points(0.0, 0.0, 5.0)
        short = good[:-1]
        with pytest.raises(ValueError):
            subtractive_section_loft(
                proj, body_index=0,
                sections=[
                    {"z": 2.0, "points": good},
                    {"z": 4.0, "points": short},
                    {"z": 6.0, "points": good},
                ],
            )
        # fewer than 3 sections is also rejected
        with pytest.raises(ValueError):
            subtractive_section_loft(
                proj, body_index=0,
                sections=[{"z": 2.0, "points": good}, {"z": 4.0, "points": good}],
            )

    def test_after_cuts_restores_an_island_erased_by_the_cavity_cut(self, tmp_path):
        """Regression for the FreeCAD-STL-Importer volume campaign iteration
        3 cut-then-restore fix: an island rebuilt inside a main cavity that
        is itself cut doc-level (subtractive_section_loft) must be added
        back with after_cuts=True, or the cavity cut erases it wholesale
        (this is exactly what happened on the real adapter case: vol_err
        got WORSE, not better, until this fix landed -- the socket-ring
        island was silently erased by the post-body cavity cut).

        A 10x10x10 block gets a square cavity cut (5x5 cross-section,
        z=2..8), then a round island (radius 2, height 2, centered in the
        cavity, fully inside its footprint) is added. Without after_cuts,
        the island is fused onto the body BEFORE the cavity is cut and gets
        erased along with the surrounding material; with after_cuts, it is
        fused AFTER the cavity cut and survives intact.
        """
        def ring_points(radius: float, cx: float = 5.0, cy: float = 5.0, segments: int = 32):
            return [
                [cx + radius * math.cos(2.0 * math.pi * i / segments),
                 cy + radius * math.sin(2.0 * math.pi * i / segments)]
                for i in range(segments)
            ]

        def build(name: str, after_cuts: bool) -> dict:
            proj = create_document(name=name)
            create_body(proj)
            additive_box(proj, body_index=0, length=10.0, width=10.0, height=10.0)
            square = self._square_loft_points(5.0, 5.0, 2.5)
            subtractive_section_loft(
                proj, body_index=0,
                sections=[
                    {"z": 2.0, "points": square},
                    {"z": 5.0, "points": square},
                    {"z": 8.0, "points": square},
                ],
                ruled=True,
            )
            additive_section_loft(
                proj, body_index=0,
                sections=[
                    {"z": 3.0, "points": ring_points(2.0)},
                    {"z": 4.0, "points": ring_points(2.0)},
                    {"z": 5.0, "points": ring_points(2.0)},
                ],
                ruled=True,
                after_cuts=after_cuts,
            )
            return proj

        volumes = {}
        for label, after_cuts in (("without_after_cuts", False), ("with_after_cuts", True)):
            proj = build(f"AfterCuts_{label}", after_cuts)
            output = str(tmp_path / f"after_cuts_{label}.stl")
            export_project(proj, output, preset="stl")
            volumes[label] = _stl_mesh_volume(output)

        # the island's volume (radius 2, height 2) is what after_cuts is
        # supposed to preserve
        island_volume = math.pi * (2.0 ** 2) * 2.0
        difference = volumes["with_after_cuts"] - volumes["without_after_cuts"]
        assert difference > 0.5 * island_volume, (
            f"expected after_cuts to preserve ~{island_volume:.1f} mm^3 of island "
            f"material that the plain (non-deferred) build erases; got "
            f"without={volumes['without_after_cuts']:.1f}, "
            f"with={volumes['with_after_cuts']:.1f}, diff={difference:.1f}"
        )
        print(
            f"\n  after_cuts preserved {difference:.2f} mm^3 "
            f"(island volume ~{island_volume:.2f} mm^3); "
            f"without={volumes['without_after_cuts']:.2f}, with={volumes['with_after_cuts']:.2f}"
        )

    def test_stack_band_outline_pads_attach_to_named_datum_planes(self, tmp_path):
        """Designer-tree Phase 2, task a: a padded_outline_stack (one Pad per
        measured z-band, sketch named "BaseOutline" / "BaseOutlineBandN")
        must attach each band's real outline sketch to a named
        DP_BaseOutlineStack_band<N>_z<...> datum plane instead of a raw
        Placement offset, and carry a construction-only reference sketch
        recording the band's measured bbox -- with the padded solid's volume
        unchanged from the raw-offset path (same two 10x10 squares stacked
        2mm then 3mm tall used here summing to a simple two-block volume).
        """
        from cli_anything.freecad.utils.freecad_backend import run_macro_content

        proj = create_document(name="StackBands")
        create_body(proj, name="MainBody")

        create_sketch(proj, name="BaseOutline", plane="XY", offset=0.0)
        pts = [[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0]]
        for i in range(4):
            add_line(proj, 0, start=pts[i], end=pts[(i + 1) % 4])
        close_sketch(proj, 0)
        pad(proj, 0, 0, length=2.0)

        create_sketch(proj, name="BaseOutlineBand2", plane="XY", offset=2.0)
        for i in range(4):
            add_line(proj, 1, start=pts[i], end=pts[(i + 1) % 4])
        close_sketch(proj, 1)
        pad(proj, 0, 1, length=3.0)

        output_stl = str(tmp_path / "stack_bands.stl")
        export_project(proj, output_stl, preset="stl")
        volume = _stl_mesh_volume(output_stl)
        expected_volume = 10.0 * 10.0 * (2.0 + 3.0)
        assert abs(volume - expected_volume) < 0.001 * expected_volume, (
            f"expected ~{expected_volume:.3f} mm^3, got {volume:.3f} mm^3"
        )

        output_fcstd = str(tmp_path / "stack_bands.FCStd")
        export_project(proj, output_fcstd, preset="fcstd")

        inspect_script = f"""
import FreeCAD
import json

doc = FreeCAD.openDocument({output_fcstd!r})
planes = [o for o in doc.Objects if o.TypeId == 'PartDesign::Plane']
sketches = [o for o in doc.Objects if o.TypeId == 'Sketcher::SketchObject']
pads = [o for o in doc.Objects if o.TypeId == 'PartDesign::Pad']

result = {{
    "plane_labels": sorted(o.Label for o in planes),
    "ref_sketch_constraints": sorted(
        o.ConstraintCount for o in sketches if o.Label.endswith('_ref')
    ),
    "pad_profiles_attached_to_plane": [
        p.Profile[0].AttachmentSupport[0][0].TypeId == 'PartDesign::Plane'
        for p in pads
        if p.Profile
    ],
}}
print("RESULT_JSON:" + json.dumps(result))
"""
        run_result = run_macro_content(inspect_script, timeout=60)
        assert run_result["returncode"] == 0, run_result["stderr"]
        json_line = next(
            line for line in run_result["stdout"].splitlines() if line.startswith("RESULT_JSON:")
        )
        result = json.loads(json_line[len("RESULT_JSON:"):])

        assert len(result["plane_labels"]) == 2
        assert all(label.startswith("DP_BaseOutlineStack_band") for label in result["plane_labels"])
        assert result["plane_labels"][0].startswith("DP_BaseOutlineStack_band1_z")
        assert result["plane_labels"][1].startswith("DP_BaseOutlineStack_band2_z")
        assert len(result["ref_sketch_constraints"]) == 2
        assert all(count >= 2 for count in result["ref_sketch_constraints"])
        assert result["pad_profiles_attached_to_plane"] == [True, True]

        print(f"\n  stack band structure: planes={result['plane_labels']}, "
              f"ref constraints={result['ref_sketch_constraints']}, "
              f"volume={volume:.2f} mm^3 (expected {expected_volume:.2f})")

    def test_section_loft_band_gets_reference_documentation_without_changing_geometry(self, tmp_path):
        """Designer-tree Phase 2, task a (section-loft-band case): an
        additive_section_loft must gain a band datum plane + construction
        reference sketch documenting its footprint, while the loft's own
        geometry (a raw OCCT Part.makeLoft) stays byte-for-byte the frustum
        volume the pre-Phase-2 regression test already proves.
        """
        from cli_anything.freecad.utils.freecad_backend import run_macro_content

        cx, cy = 7.0, 5.0
        proj = create_document(name="LoftBandDocs")
        create_body(proj)
        additive_box(proj, body_index=0, length=10.0, width=10.0, height=2.0,
                     position=[cx - 5.0, cy - 5.0, 0.0])
        sections = [
            {"z": 2.0, "points": self._square_loft_points(cx, cy, 5.0)},
            {"z": 4.0, "points": self._square_loft_points(cx, cy, 4.0)},
            {"z": 6.0, "points": self._square_loft_points(cx, cy, 5.0)},
        ]
        additive_section_loft(proj, body_index=0, sections=sections, ruled=True)

        output_stl = str(tmp_path / "loft_band_docs.stl")
        export_project(proj, output_stl, preset="stl")
        volume = _stl_mesh_volume(output_stl)

        box_volume = 10.0 * 10.0 * 2.0
        seg1 = 2.0 * (10.0 ** 2 + 10.0 * 8.0 + 8.0 ** 2) / 3.0
        seg2 = 2.0 * (8.0 ** 2 + 8.0 * 10.0 + 10.0 ** 2) / 3.0
        expected_total = box_volume + seg1 + seg2
        assert abs(volume - expected_total) < 0.05 * expected_total, (
            f"expected ~{expected_total:.1f} mm^3, got {volume:.1f} mm^3 -- the "
            f"reference documentation must not perturb the loft's own geometry"
        )

        output_fcstd = str(tmp_path / "loft_band_docs.FCStd")
        export_project(proj, output_fcstd, preset="fcstd")
        inspect_script = f"""
import FreeCAD
import json

doc = FreeCAD.openDocument({output_fcstd!r})
planes = [o.Label for o in doc.Objects if o.TypeId == 'PartDesign::Plane']
ref_sketches = [o for o in doc.Objects if o.TypeId == 'Sketcher::SketchObject' and o.Label.endswith('_ref')]
print("RESULT_JSON:" + json.dumps({{
    "plane_labels": planes,
    "ref_constraint_counts": [o.ConstraintCount for o in ref_sketches],
    "ref_non_driving": [
        not o.Constraints[i].Driving
        for o in ref_sketches
        for i in range(o.ConstraintCount)
    ],
}}))
"""
        run_result = run_macro_content(inspect_script, timeout=60)
        assert run_result["returncode"] == 0, run_result["stderr"]
        json_line = next(
            line for line in run_result["stdout"].splitlines() if line.startswith("RESULT_JSON:")
        )
        result = json.loads(json_line[len("RESULT_JSON:"):])
        # two datum planes exist -- one from the base box (Phase 1's
        # dimensioned-primitive path) and one for this section-loft band;
        # only the loft's carries a "_ref" reference sketch.
        assert len(result["plane_labels"]) == 2
        assert all(label.startswith("DP_") for label in result["plane_labels"])
        assert any("section_loft" in label for label in result["plane_labels"])
        assert len(result["ref_constraint_counts"]) == 1
        assert result["ref_constraint_counts"][0] == 2
        assert all(result["ref_non_driving"]), "band reference dimensions must be non-driving"

        print(f"\n  loft band docs: plane={result['plane_labels']}, "
              f"volume={volume:.2f} mm^3 (expected {expected_total:.2f})")

    def test_constant_radius_band_converts_to_dimensioned_circle_pad(self, tmp_path):
        """Designer-tree Phase 2, task b: a standalone constant-radius
        section-loft band (the ring_bands/bore_bands/recess_bands shape,
        not deferred and not preceded by any other doc-level op on its
        body) must be realised as a real dimensioned circle sketch + Pad on
        a named band datum plane (Phase 1 helper) instead of a raw N-gon
        OCCT loft -- trading the polygon approximation's area deficit for
        an exact analytic cylinder volume.
        """
        from cli_anything.freecad.utils.freecad_backend import run_macro_content

        def ring_points(radius, cx=4.0, cy=3.0, segments=24):
            return [
                [cx + radius * math.cos(2.0 * math.pi * i / segments),
                 cy + radius * math.sin(2.0 * math.pi * i / segments)]
                for i in range(segments)
            ]

        cx, cy, radius = 4.0, 3.0, 3.0
        points = ring_points(radius)
        proj = create_document(name="ConstantRadiusBand")
        create_body(proj, name="MainBody")
        # A small, far-away base feature: additive_section_loft requires an
        # existing body feature to attach to, and this one (a plain
        # dimensioned box, Phase 1's path) does not set body_has_doc_level,
        # so the circular band below is still the body's first doc-level op
        # and remains eligible for conversion.
        additive_box(proj, body_index=0, length=1.0, width=1.0, height=1.0,
                     position=[-10.0, -10.0, 0.0])
        additive_section_loft(
            proj, body_index=0,
            sections=[
                {"z": 0.0, "points": points},
                {"z": 3.0, "points": points},
                {"z": 6.0, "points": points},
            ],
            ruled=True,
        )

        output_stl = str(tmp_path / "constant_radius.stl")
        export_project(proj, output_stl, preset="stl")
        volume = _stl_mesh_volume(output_stl)

        base_box_volume = 1.0 * 1.0 * 1.0
        exact_cylinder_volume = math.pi * radius ** 2 * 6.0
        ngon_area = 0.5 * 24 * radius ** 2 * math.sin(2.0 * math.pi / 24)
        ngon_volume = ngon_area * 6.0
        expected_total = base_box_volume + exact_cylinder_volume
        ngon_total = base_box_volume + ngon_volume
        assert abs(volume - expected_total) < 0.01 * expected_total, (
            f"expected the true-circle Pad volume ~{expected_total:.2f} mm^3, "
            f"got {volume:.2f} mm^3 (24-gon loft would have given ~{ngon_total:.2f})"
        )
        assert abs(volume - ngon_total) > 0.005 * exact_cylinder_volume, (
            "volume should differ measurably from the old 24-gon loft approximation, "
            "confirming a real conversion happened rather than a no-op"
        )

        output_fcstd = str(tmp_path / "constant_radius.FCStd")
        export_project(proj, output_fcstd, preset="fcstd")
        inspect_script = f"""
import FreeCAD
import json

doc = FreeCAD.openDocument({output_fcstd!r})
pads = [o for o in doc.Objects if o.TypeId == 'PartDesign::Pad']
planes = [o.Label for o in doc.Objects if o.TypeId == 'PartDesign::Plane']
sketches = [o for o in doc.Objects if o.TypeId == 'Sketcher::SketchObject']
radius_constraints = []
for s in sketches:
    for c in s.Constraints:
        if c.Type == 'Radius':
            radius_constraints.append(c.Value)
print("RESULT_JSON:" + json.dumps({{
    "pad_count": len(pads),
    "plane_labels": planes,
    "radius_constraints": radius_constraints,
}}))
"""
        run_result = run_macro_content(inspect_script, timeout=60)
        assert run_result["returncode"] == 0, run_result["stderr"]
        json_line = next(
            line for line in run_result["stdout"].splitlines() if line.startswith("RESULT_JSON:")
        )
        result = json.loads(json_line[len("RESULT_JSON:"):])
        # one Pad for the base box (Phase 1 dimensioned-box path) plus one
        # for the converted constant-radius band.
        assert result["pad_count"] == 2, "the constant-radius band must become a real PartDesign::Pad"
        assert len(result["plane_labels"]) == 2
        assert any(abs(value - radius) < 1e-3 for value in result["radius_constraints"]), (
            f"expected a Radius constraint ~{radius}, got {result['radius_constraints']}"
        )

        print(f"\n  constant-radius band: volume={volume:.2f} mm^3 "
              f"(exact total {expected_total:.2f}, old-24gon total {ngon_total:.2f}), "
              f"pads={result['pad_count']}")

    def test_after_cuts_band_stays_doc_level_with_reference_documentation(self, tmp_path):
        """Designer-tree Phase 2, task b's doc-level fallback: an after_cuts
        constant-radius band (ring_bands/bore_bands' real shape) must NOT
        be converted to a Pad -- it still needs the doc-level chain onto the
        cavity cut's result -- but must still gain the band datum plane +
        reference sketch documentation, and its preserved-island volume
        behaviour (the whole point of after_cuts) must be unaffected.
        """
        from cli_anything.freecad.utils.freecad_backend import run_macro_content

        def ring_points(radius, cx=5.0, cy=5.0, segments=32):
            return [
                [cx + radius * math.cos(2.0 * math.pi * i / segments),
                 cy + radius * math.sin(2.0 * math.pi * i / segments)]
                for i in range(segments)
            ]

        proj = create_document(name="AfterCutsBandDocs")
        create_body(proj)
        additive_box(proj, body_index=0, length=10.0, width=10.0, height=10.0)
        square = self._square_loft_points(5.0, 5.0, 2.5)
        subtractive_section_loft(
            proj, body_index=0,
            sections=[
                {"z": 2.0, "points": square},
                {"z": 5.0, "points": square},
                {"z": 8.0, "points": square},
            ],
            ruled=True,
        )
        additive_section_loft(
            proj, body_index=0,
            sections=[
                {"z": 3.0, "points": ring_points(2.0)},
                {"z": 4.0, "points": ring_points(2.0)},
                {"z": 5.0, "points": ring_points(2.0)},
            ],
            ruled=True,
            after_cuts=True,
        )

        output_fcstd = str(tmp_path / "after_cuts_band_docs.FCStd")
        export_project(proj, output_fcstd, preset="fcstd")
        inspect_script = f"""
import FreeCAD
import json

doc = FreeCAD.openDocument({output_fcstd!r})
pads = [o for o in doc.Objects if o.TypeId == 'PartDesign::Pad']
planes = [o.Label for o in doc.Objects if o.TypeId == 'PartDesign::Plane']
fuses = [o for o in doc.Objects if o.TypeId == 'Part::Fuse']
print("RESULT_JSON:" + json.dumps({{
    "pad_count": len(pads),
    "plane_labels": planes,
    "fuse_count": len(fuses),
}}))
"""
        run_result = run_macro_content(inspect_script, timeout=60)
        assert run_result["returncode"] == 0, run_result["stderr"]
        json_line = next(
            line for line in run_result["stdout"].splitlines() if line.startswith("RESULT_JSON:")
        )
        result = json.loads(json_line[len("RESULT_JSON:"):])
        # exactly one Pad exists -- the base box (Phase 1's dimensioned-primitive
        # path, unrelated to this feature); the after_cuts ring must NOT add a
        # second Pad of its own, i.e. it must stay a doc-level Part::Fuse.
        assert result["pad_count"] == 1, (
            "an after_cuts band must stay a doc-level Part::Fuse, not become its own Pad"
        )
        assert result["fuse_count"] >= 1
        # three datum planes: the base box (Phase 1), the main-cavity
        # subtractive band, and the after_cuts ring -- all doc-level except
        # the box's Pad.
        assert len(result["plane_labels"]) == 3
        assert all(label.startswith("DP_") for label in result["plane_labels"])

        print(f"\n  after_cuts band structure: pads={result['pad_count']}, "
              f"fuses={result['fuse_count']}, plane={result['plane_labels']}")

    def test_bayonet_groove_gets_reference_sketch_anchored_to_body_axis(self, tmp_path):
        """Designer-tree Phase 2, task c: a bayonet_groove must gain a
        construction reference sketch on a datum plane at the channel's
        z-start, carrying non-driving Angle/Distance reference dimensions
        and an ExternalGeometry import of the body's helper axis -- while
        the cut's own geometry (and its volume) stays exactly what the
        pre-existing off-origin regression test already proves.
        """
        from cli_anything.freecad.utils.freecad_backend import run_macro_content

        proj = create_document(name="BayonetRefSketch")
        create_body(proj, name="MainBody")
        additive_cylinder(proj, body_index=0, radius=6.236, height=7.87,
                           position=[7.2, 7.15, 8.17])
        segments = [
            {"kind": "circumferential", "angle0": 69.0, "angle1": 157.0,
             "z0": 8.9, "z1": 10.4, "wall_radius": 6.23, "depth": 0.95},
        ]
        bayonet_groove(proj, body_index=0, segments=segments,
                       wall_radius=6.256, depth=0.9,
                       center_x=7.2, center_y=7.15, symmetry=1)

        output_fcstd = str(tmp_path / "bayonet_ref.FCStd")
        export_project(proj, output_fcstd, preset="fcstd")
        inspect_script = f"""
import FreeCAD
import json

doc = FreeCAD.openDocument({output_fcstd!r})
planes = [o.Label for o in doc.Objects if o.TypeId == 'PartDesign::Plane']
ref_sketches = [o for o in doc.Objects if o.TypeId == 'Sketcher::SketchObject' and o.Label.endswith('_ref')]
info = []
for s in ref_sketches:
    info.append({{
        "external_geometry_count": len(s.ExternalGeometry),
        "constraint_types": sorted(set(c.Type for c in s.Constraints)),
        "non_driving_count": sum(1 for c in s.Constraints if not c.Driving),
    }})
print("RESULT_JSON:" + json.dumps({{"plane_labels": planes, "ref_sketches": info}}))
"""
        run_result = run_macro_content(inspect_script, timeout=60)
        assert run_result["returncode"] == 0, run_result["stderr"]
        json_line = next(
            line for line in run_result["stdout"].splitlines() if line.startswith("RESULT_JSON:")
        )
        result = json.loads(json_line[len("RESULT_JSON:"):])
        assert len(result["plane_labels"]) == 2  # cylinder's own + the bayonet's
        assert any("bayonet" in label.lower() for label in result["plane_labels"])
        assert len(result["ref_sketches"]) == 1
        ref = result["ref_sketches"][0]
        assert ref["external_geometry_count"] >= 1, "must import the body axis as external geometry"
        assert "Angle" in ref["constraint_types"]
        assert ref["non_driving_count"] >= 2  # depth reference + angle reference

        print(f"\n  bayonet reference sketch: planes={result['plane_labels']}, "
              f"external_geo={ref['external_geometry_count']}, "
              f"constraints={ref['constraint_types']}")

    @pytest.mark.skipif(not _has_freecad_preview(), reason="GUI-capable FreeCAD not installed")
    def test_preview_capture_bundle(self, tmp_path):
        proj = create_document(name="PreviewPart")
        add_part(proj, "box", name="MainBlock", params={"length": 30, "width": 20, "height": 12})
        add_part(
            proj,
            "cylinder",
            name="SideBoss",
            params={"radius": 4, "height": 14},
            position=[18, 0, 0],
        )

        project_path = str(tmp_path / "preview.json")
        save_document(proj, project_path)

        sess = Session()
        sess.set_project(proj, path=project_path)

        manifest = preview_mod.capture(sess, root_dir=str(tmp_path), force=True)
        assert manifest["software"] == "freecad"
        assert manifest["bundle_kind"] == "capture"
        assert manifest["status"] in ("ok", "partial")

        hero_path = _artifact_path(manifest, "hero")
        front_path = _artifact_path(manifest, "front")
        top_path = _artifact_path(manifest, "top")
        right_path = _artifact_path(manifest, "right")
        _assert_png(hero_path)
        _assert_png(front_path)
        _assert_png(top_path)
        _assert_png(right_path)

        latest = preview_mod.latest(project_path=project_path, recipe="quick", root_dir=str(tmp_path))
        assert latest["bundle_id"] == manifest["bundle_id"]

        print(f"\n  FreeCAD preview bundle: {manifest['_bundle_dir']}")
        print(f"  FreeCAD preview hero: {hero_path}")
        print(f"  FreeCAD preview front: {front_path}")
        print(f"  FreeCAD preview top: {top_path}")
        print(f"  FreeCAD preview right: {right_path}")

    @pytest.mark.skipif(not _has_freecad_preview(), reason="GUI-capable FreeCAD not installed")
    def test_preview_capture_bundle_body_patterns(self, tmp_path):
        proj = create_document(name="PreviewBodyTower")
        create_body(proj, name="TowerBody")
        additive_box(proj, 0, length=34, width=34, height=18, position=[0, 0, 0])
        additive_box(proj, 0, length=8, width=6, height=4, position=[21, 0, 7])
        polar_pattern(proj, 0, axis="Z", angle=360, occurrences=4)
        additive_box(proj, 0, length=30, width=30, height=16, position=[0, 0, 18])
        additive_box(proj, 0, length=7, width=5, height=3, position=[18.5, 0, 24])
        polar_pattern(proj, 0, axis="Z", angle=360, occurrences=4)
        additive_cylinder(proj, 0, radius=2.2, height=18, position=[0, 0, 34])
        additive_cone(proj, 0, radius1=2.5, radius2=0.4, height=10, position=[0, 0, 52])

        project_path = str(tmp_path / "preview_body.json")
        save_document(proj, project_path)

        sess = Session()
        sess.set_project(proj, path=project_path)

        manifest = preview_mod.capture(sess, root_dir=str(tmp_path), force=True)
        assert manifest["software"] == "freecad"
        assert manifest["bundle_kind"] == "capture"
        assert manifest["status"] in ("ok", "partial")

        hero_path = _artifact_path(manifest, "hero")
        front_path = _artifact_path(manifest, "front")
        _assert_png_not_blank(hero_path)
        _assert_png_not_blank(front_path)

        print(f"\n  FreeCAD body preview bundle: {manifest['_bundle_dir']}")
        print(f"  FreeCAD body preview hero: {hero_path}")
        print(f"  FreeCAD body preview front: {front_path}")


# =========================================================================
# 3. CLI subprocess tests
# =========================================================================

class TestCLISubprocess:
    """Test the CLI entry-point via subprocess invocations."""

    @pytest.fixture(autouse=True)
    def _cli_cmd(self):
        """Resolve the CLI command once for all tests."""
        self.cli = _resolve_cli("cli-anything-freecad")

    def _run(self, *args: str, timeout: int = 30, **kwargs) -> subprocess.CompletedProcess:
        """Run a CLI command and return the result."""
        cmd = self.cli + list(args)
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            **kwargs,
        )

    def test_help(self):
        """--help returns exit code 0 and prints usage."""
        result = self._run("--help")
        assert result.returncode == 0, (
            f"--help failed (rc={result.returncode}): {result.stderr}"
        )
        assert "freecad" in result.stdout.lower() or "usage" in result.stdout.lower(), (
            f"Unexpected help output: {result.stdout[:200]}"
        )
        print(f"\n  --help: rc={result.returncode}, {len(result.stdout)} chars")

    def test_document_new_json(self, tmp_path):
        """'--json document new -o <path>' creates valid JSON output."""
        out_file = str(tmp_path / "new_doc.json")
        result = self._run("--json", "document", "new",
                           "--name", "TestDoc", "-o", out_file)
        assert result.returncode == 0, (
            f"document new failed (rc={result.returncode}): {result.stderr}"
        )

        # stdout should be valid JSON
        data = json.loads(result.stdout)
        assert data["name"] == "TestDoc"
        assert "version" in data

        # File should exist
        assert os.path.isfile(out_file)
        print(f"\n  document new: {out_file} ({os.path.getsize(out_file):,} bytes)")

    def test_part_add_json(self, tmp_path):
        """Create doc then add a part, verify JSON output."""
        proj_file = str(tmp_path / "part_add.json")

        # Create document
        r1 = self._run("--json", "document", "new",
                        "--name", "PartTest", "-o", proj_file)
        assert r1.returncode == 0, f"doc new failed: {r1.stderr}"

        # Add a box part
        r2 = self._run("--json", "-p", proj_file, "part", "add", "box",
                        "--name", "MyBox", "-P", "length=30")
        assert r2.returncode == 0, f"part add failed: {r2.stderr}"

        data = json.loads(r2.stdout)
        assert data["type"] == "box"
        assert data["name"] == "MyBox"
        assert data["params"]["length"] == 30.0

        print(f"\n  part add: {data['name']} (type={data['type']})")

    def test_part_list_json(self, tmp_path):
        """Create doc, add parts, list them, verify count."""
        proj_file = str(tmp_path / "part_list.json")

        # Create document
        self._run("--json", "document", "new",
                   "--name", "ListTest", "-o", proj_file)

        # Add two parts
        self._run("--json", "-p", proj_file, "part", "add", "box", "--name", "A")
        self._run("--json", "-p", proj_file, "part", "add", "cylinder", "--name", "B")

        # List parts
        r = self._run("--json", "-p", proj_file, "part", "list")
        assert r.returncode == 0, f"part list failed: {r.stderr}"

        parts = json.loads(r.stdout)
        assert isinstance(parts, list)
        assert len(parts) == 2
        names = {p["name"] for p in parts}
        assert "A" in names
        assert "B" in names

        print(f"\n  part list: {len(parts)} parts ({names})")

    def test_part_align_and_bounds_json(self, tmp_path):
        """Create parts, align one to another, and verify bbox output."""
        proj_file = str(tmp_path / "part_align.json")

        self._run("--json", "document", "new", "--name", "AlignTest", "-o", proj_file)
        self._run(
            "--json",
            "-p",
            proj_file,
            "part",
            "add",
            "box",
            "--name",
            "Base",
            "-P",
            "length=20",
            "-P",
            "width=10",
            "-P",
            "height=6",
        )
        self._run(
            "--json",
            "-p",
            proj_file,
            "part",
            "add",
            "box",
            "--name",
            "Cap",
            "-P",
            "length=8",
            "-P",
            "width=6",
            "-P",
            "height=4",
            "-pos",
            "100,50,20",
        )

        aligned = self._run(
            "--json",
            "-p",
            proj_file,
            "part",
            "align",
            "1",
            "0",
            "--x",
            "min",
            "--to-x",
            "max",
            "--y",
            "center",
            "--to-y",
            "center",
            "--z",
            "min",
            "--to-z",
            "max",
        )
        assert aligned.returncode == 0, aligned.stderr
        align_payload = json.loads(aligned.stdout)
        assert align_payload["placement"]["position"] == [20.0, 2.0, 6.0]

        bounds = self._run("--json", "-p", proj_file, "part", "bounds", "1")
        assert bounds.returncode == 0, bounds.stderr
        bounds_payload = json.loads(bounds.stdout)
        world = bounds_payload["world_bounding_box"]
        assert world["min"]["x"] == pytest.approx(20.0)
        assert world["center"]["y"] == pytest.approx(5.0)
        assert world["min"]["z"] == pytest.approx(6.0)

    def test_full_workflow_subprocess(self, tmp_path):
        """Full subprocess workflow: create -> box -> cylinder -> boolean cut -> list."""
        proj_file = str(tmp_path / "workflow.json")

        # 1. Create document
        r = self._run("--json", "document", "new",
                       "--name", "WorkflowTest", "-o", proj_file)
        assert r.returncode == 0, f"doc new: {r.stderr}"

        # 2. Add box
        r = self._run("--json", "-p", proj_file, "part", "add", "box",
                       "--name", "Base", "-P", "length=20", "-P", "width=20",
                       "-P", "height=20")
        assert r.returncode == 0, f"add box: {r.stderr}"
        box = json.loads(r.stdout)
        assert box["name"] == "Base"

        # 3. Add cylinder
        r = self._run("--json", "-p", proj_file, "part", "add", "cylinder",
                       "--name", "Hole", "-P", "radius=5", "-P", "height=30",
                       "-pos", "10,10,-5")
        assert r.returncode == 0, f"add cylinder: {r.stderr}"
        cyl = json.loads(r.stdout)
        assert cyl["name"] == "Hole"

        # 4. Boolean cut
        r = self._run("--json", "-p", proj_file,
                       "part", "boolean", "cut", "0", "1")
        assert r.returncode == 0, f"boolean cut: {r.stderr}"
        cut = json.loads(r.stdout)
        assert cut["type"] == "cut"

        # 5. List parts -- should have 3 (box, cylinder, cut-result)
        r = self._run("--json", "-p", proj_file, "part", "list")
        assert r.returncode == 0, f"part list: {r.stderr}"
        parts = json.loads(r.stdout)
        assert len(parts) == 3, f"Expected 3 parts, got {len(parts)}: {parts}"

        # Verify visibility: first two hidden, cut result visible
        visible_count = sum(1 for p in parts if p.get("visible", True))
        assert visible_count >= 1, "At least the cut result should be visible"

        type_names = [p["type"] for p in parts]
        assert "cut" in type_names, f"No 'cut' part found in types: {type_names}"

        print(f"\n  Workflow complete: {len(parts)} parts")
        for p in parts:
            print(f"    {p['name']}: type={p['type']}, visible={p.get('visible', '?')}")

    @pytest.mark.skipif(not _has_freecad_preview(), reason="GUI-capable FreeCAD not installed")
    def test_preview_capture_subprocess(self, tmp_path):
        proj_file = str(tmp_path / "preview_cli.json")

        proj = create_document(name="PreviewCLI")
        add_part(proj, "box", name="Body", params={"length": 24, "width": 18, "height": 10})
        save_document(proj, proj_file)

        result = self._run(
            "--json",
            "-p",
            proj_file,
            "preview",
            "capture",
            "--root-dir",
            str(tmp_path),
            timeout=240,
        )
        assert result.returncode == 0, result.stderr

        manifest = json.loads(result.stdout)
        assert manifest["software"] == "freecad"
        hero_path = _artifact_path(manifest, "hero")
        _assert_png(hero_path)

        latest = self._run(
            "--json",
            "preview",
            "latest",
            "--recipe",
            "quick",
            "--root-dir",
            str(tmp_path),
            timeout=60,
        )
        assert latest.returncode == 0, latest.stderr
        latest_manifest = json.loads(latest.stdout)
        assert latest_manifest["bundle_id"] == manifest["bundle_id"]

        print(f"\n  FreeCAD preview bundle: {manifest['_bundle_dir']}")
        print(f"  FreeCAD preview hero: {hero_path}")

    @pytest.mark.skipif(not _has_freecad_preview(), reason="GUI-capable FreeCAD not installed")
    def test_motion_render_frames_subprocess(self, tmp_path):
        proj_file = str(tmp_path / "motion_frames.json")
        frames_dir = str(tmp_path / "motion-frames")

        created = self._run(
            "--json",
            "document",
            "new",
            "--name",
            "MotionFrames",
            "-o",
            proj_file,
        )
        assert created.returncode == 0, created.stderr

        added = self._run(
            "--json",
            "-p",
            proj_file,
            "part",
            "add",
            "box",
            "--name",
            "Mover",
            "-P",
            "length=20",
            "-P",
            "width=12",
            "-P",
            "height=8",
        )
        assert added.returncode == 0, added.stderr

        motion_new = self._run(
            "--json",
            "-p",
            proj_file,
            "motion",
            "new",
            "--name",
            "Drive",
            "--duration",
            "1.0",
            "--fps",
            "4",
            "--camera",
            "hero",
            "--width",
            "640",
            "--height",
            "480",
        )
        assert motion_new.returncode == 0, motion_new.stderr

        k0 = self._run("--json", "-p", proj_file, "motion", "keyframe", "0", "part", "0", "0.0")
        assert k0.returncode == 0, k0.stderr

        k1 = self._run(
            "--json",
            "-p",
            proj_file,
            "motion",
            "keyframe",
            "0",
            "part",
            "0",
            "1.0",
            "--position",
            "35,0,0",
            "--rotation",
            "0,0,45",
        )
        assert k1.returncode == 0, k1.stderr

        rendered = self._run(
            "--json",
            "-p",
            proj_file,
            "motion",
            "render-frames",
            "0",
            frames_dir,
            "--overwrite",
            timeout=240,
        )
        assert rendered.returncode == 0, rendered.stderr
        payload = json.loads(rendered.stdout)
        assert payload["frame_count"] == 5
        assert payload["method"] == "freecad-gui-sequence"

        sequence_path = payload["sequence_path"]
        assert os.path.isfile(sequence_path)
        with open(sequence_path, "r", encoding="utf-8") as fh:
            sequence = json.load(fh)
        assert sequence["frame_count"] == 5

        first_frame = os.path.join(payload["output_dir"], sequence["frames"][0]["path"])
        last_frame = os.path.join(payload["output_dir"], sequence["frames"][-1]["path"])
        _assert_png_not_blank(first_frame)
        _assert_png_not_blank(last_frame)
        _assert_images_differ(first_frame, last_frame)

    @pytest.mark.skipif(not (_has_freecad_preview() and _has_ffmpeg()), reason="GUI-capable FreeCAD and ffmpeg required")
    def test_motion_render_video_subprocess(self, tmp_path):
        proj_file = str(tmp_path / "motion_video.json")
        frames_dir = str(tmp_path / "motion-video-frames")
        video_path = str(tmp_path / "motion.mp4")

        proj = create_document(name="MotionVideo")
        add_part(proj, "box", name="Mover", params={"length": 20, "width": 12, "height": 8})
        save_document(proj, proj_file)

        assert self._run(
            "--json",
            "-p",
            proj_file,
            "motion",
            "new",
            "--name",
            "Drive",
            "--duration",
            "1.0",
            "--fps",
            "4",
            "--camera",
            "hero",
            "--width",
            "640",
            "--height",
            "480",
        ).returncode == 0

        assert self._run(
            "--json", "-p", proj_file, "motion", "keyframe", "0", "part", "0", "0.0"
        ).returncode == 0
        assert self._run(
            "--json",
            "-p",
            proj_file,
            "motion",
            "keyframe",
            "0",
            "part",
            "0",
            "1.0",
            "--position",
            "35,0,0",
            "--rotation",
            "0,0,90",
        ).returncode == 0

        rendered = self._run(
            "--json",
            "-p",
            proj_file,
            "motion",
            "render-video",
            "0",
            video_path,
            "--overwrite",
            "--frames-dir",
            frames_dir,
            timeout=300,
        )
        assert rendered.returncode == 0, rendered.stderr
        payload = json.loads(rendered.stdout)
        assert payload["format"] == "mp4"
        assert os.path.isfile(video_path)
        assert os.path.getsize(video_path) > 0
        assert payload["frame_count"] == 5
        assert payload["frames_dir"] == os.path.abspath(frames_dir)
        assert os.path.isfile(payload["sequence_path"])

    @pytest.mark.skipif(not _has_freecad_preview(), reason="GUI-capable FreeCAD not installed")
    def test_preview_live_poll_auto_refresh(self, tmp_path):
        proj_file = str(tmp_path / "preview_live_poll.json")
        live_root = str(tmp_path / "live-root")

        proj = create_document(name="PreviewLiveCLI")
        add_part(proj, "box", name="BaseBody", params={"length": 24, "width": 18, "height": 10})
        save_document(proj, proj_file)

        started = self._run(
            "--json",
            "-p",
            proj_file,
            "preview",
            "live",
            "start",
            "--recipe",
            "quick",
            "--mode",
            "poll",
            "--source-poll-ms",
            "500",
            "--poll-ms",
            "700",
            "--root-dir",
            live_root,
            timeout=240,
        )
        assert started.returncode == 0, started.stderr
        started_payload = json.loads(started.stdout)
        session_path = started_payload["_session_path"]
        assert started_payload["bundle_count"] == 1
        assert started_payload["live_mode"] == "poll"

        try:
            changed = self._run(
                "--json",
                "-p",
                proj_file,
                "part",
                "add",
                "cylinder",
                "--name",
                "SideBoss",
                "-P",
                "radius=4",
                "-P",
                "height=14",
                "-pos",
                "18,0,0",
                timeout=60,
            )
            assert changed.returncode == 0, changed.stderr

            session_payload = _wait_for_live_bundle_count(session_path, 2, timeout_s=30.0)
            assert session_payload["bundle_count"] >= 2
            assert session_payload["source_state"]["last_publish_reason"] == "auto-poll"
            assert session_payload["poller"]["running"] is True

            current_manifest_path = session_payload["current_manifest_path"]
            assert os.path.isfile(current_manifest_path)
            with open(current_manifest_path, "r", encoding="utf-8") as fh:
                current_manifest = json.load(fh)
            current_manifest["_bundle_dir"] = os.path.dirname(current_manifest_path)
            hero_path = _artifact_path(current_manifest, "hero")
            _assert_png(hero_path)

            print(f"\n  FreeCAD poll session: {os.path.dirname(session_path)}")
            print(f"  FreeCAD live hero: {hero_path}")
        finally:
            stopped = self._run(
                "--json",
                "-p",
                proj_file,
                "preview",
                "live",
                "stop",
                "--recipe",
                "quick",
                "--root-dir",
                live_root,
                timeout=60,
            )
            assert stopped.returncode == 0, stopped.stderr
