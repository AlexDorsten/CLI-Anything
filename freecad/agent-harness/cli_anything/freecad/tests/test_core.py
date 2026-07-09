"""
Comprehensive unit tests for the cli-anything-freecad core modules.

All tests use synthetic data and require no external dependencies
beyond pytest.
"""

import json
import math
import os
from pathlib import Path

import pytest

from cli_anything.freecad.core.document import (
    PROFILES,
    create_document,
    get_document_info,
    list_profiles,
    open_document,
    save_document,
)
from cli_anything.freecad.core.parts import (
    PRIMITIVES,
    add_part,
    align_part,
    boolean_op,
    get_part,
    list_parts,
    part_bounds,
    remove_part,
    transform_part,
)
from cli_anything.freecad.core.sketch import (
    add_arc,
    add_circle,
    add_constraint,
    add_line,
    add_rectangle,
    close_sketch,
    create_sketch,
    get_sketch,
    list_sketches,
)
from cli_anything.freecad.core.body import (
    additive_box,
    additive_cone,
    additive_cylinder,
    additive_section_loft,
    bayonet_groove,
    chamfer,
    create_body,
    datum_plane,
    datum_line,
    datum_point,
    fillet,
    get_body,
    hole_feature,
    linear_pattern,
    list_bodies,
    local_coordinate_system,
    pad,
    pocket,
    polar_pattern,
    revolution,
    subtractive_box,
    subtractive_section_loft,
    toggle_freeze,
)
from cli_anything.freecad.core.materials import (
    PRESETS,
    assign_material,
    create_material,
    get_material,
    list_materials,
    list_presets,
    set_material_property,
)
from cli_anything.freecad.core import preview as preview_mod
from cli_anything.freecad.core import motion as motion_mod
from cli_anything.freecad.core.session import Session
from cli_anything.freecad.utils import freecad_backend


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_project(**overrides):
    """Create a minimal valid project dict, applying any overrides."""
    proj = create_document(name="TestProject")
    proj.update(overrides)
    return proj


def _make_wrapper_script(path: Path) -> Path:
    path.write_text(
        "#!/bin/bash\n"
        "SCRIPT_MODE=1\n"
        "xvfb-run freecadcmd \"$@\"\n",
        encoding="utf-8",
    )
    return path


# ===========================================================================
# TestDocument
# ===========================================================================


class TestDocument:
    """Tests for the document module."""

    def test_create_default(self):
        proj = create_document()
        assert proj["name"] == "Untitled"
        assert proj["units"] == "mm"
        assert proj["version"] == "1.0"
        assert proj["parts"] == []
        assert proj["sketches"] == []
        assert proj["bodies"] == []
        assert proj["materials"] == []
        assert "created" in proj["metadata"]
        assert "modified" in proj["metadata"]
        assert "software" in proj["metadata"]

    def test_create_with_profile(self):
        proj = create_document(name="ImperialProject", profile="imperial")
        assert proj["units"] == "in"
        assert proj["name"] == "ImperialProject"

        proj2 = create_document(profile="metric_large")
        assert proj2["units"] == "m"

    def test_create_invalid_profile(self):
        with pytest.raises(ValueError, match="Unknown profile"):
            create_document(profile="nonexistent_profile")

    def test_save_and_open(self, tmp_path):
        proj = create_document(name="RoundTrip", units="mm")
        add_part(proj, "box", name="TestBox")

        filepath = str(tmp_path / "roundtrip.json")
        abs_path = save_document(proj, filepath)
        assert os.path.isfile(abs_path)

        loaded = open_document(filepath)
        assert loaded["name"] == "RoundTrip"
        assert loaded["units"] == "mm"
        assert len(loaded["parts"]) == 1
        assert loaded["parts"][0]["name"] == "TestBox"

    def test_open_nonexistent(self, tmp_path):
        missing = str(tmp_path / "does_not_exist.json")
        with pytest.raises(FileNotFoundError):
            open_document(missing)

    def test_get_info(self):
        proj = create_document(name="InfoTest")
        info = get_document_info(proj)
        assert info["name"] == "InfoTest"
        assert info["units"] == "mm"
        assert info["parts_count"] == 0
        assert info["sketches_count"] == 0
        assert info["bodies_count"] == 0
        assert info["materials_count"] == 0
        assert info["motions_count"] == 0

    def test_get_info_with_data(self):
        proj = create_document(name="DataTest")
        add_part(proj, "box")
        add_part(proj, "cylinder")
        create_sketch(proj)

        info = get_document_info(proj)
        assert info["parts_count"] == 2
        assert info["sketches_count"] == 1

    def test_list_profiles(self):
        profiles = list_profiles()
        assert isinstance(profiles, list)
        assert len(profiles) == len(PROFILES)
        names = {p["name"] for p in profiles}
        assert "default" in names
        assert "imperial" in names
        for p in profiles:
            assert "name" in p
            assert "units" in p
            assert "description" in p


# ===========================================================================
# TestParts
# ===========================================================================


class TestParts:
    """Tests for the parts module."""

    def test_add_box_defaults(self):
        proj = _make_project()
        part = add_part(proj, "box")
        assert part["type"] == "box"
        assert part["name"] == "Box"
        assert part["params"]["length"] == 10.0
        assert part["params"]["width"] == 10.0
        assert part["params"]["height"] == 10.0
        assert part["placement"]["position"] == [0.0, 0.0, 0.0]
        assert part["placement"]["rotation"] == [0.0, 0.0, 0.0]
        assert part["visible"] is True
        assert part["material_index"] is None
        assert len(proj["parts"]) == 1

    @pytest.mark.parametrize("ptype", ["box", "cylinder", "sphere", "cone", "torus", "wedge"])
    def test_add_all_primitives(self, ptype):
        proj = _make_project()
        part = add_part(proj, ptype)
        assert part["type"] == ptype
        # All default params from PRIMITIVES should be present
        for key in PRIMITIVES[ptype]:
            assert key in part["params"]
            assert part["params"][key] == PRIMITIVES[ptype][key]

    def test_add_with_position_rotation(self):
        proj = _make_project()
        part = add_part(proj, "box", position=[1.0, 2.0, 3.0], rotation=[45.0, 0.0, 90.0])
        assert part["placement"]["position"] == [1.0, 2.0, 3.0]
        assert part["placement"]["rotation"] == [45.0, 0.0, 90.0]

    def test_add_with_custom_params(self):
        proj = _make_project()
        part = add_part(proj, "box", params={"length": 20.0, "width": 5.0})
        assert part["params"]["length"] == 20.0
        assert part["params"]["width"] == 5.0
        assert part["params"]["height"] == 10.0  # default unchanged

    def test_add_invalid_type(self):
        proj = _make_project()
        with pytest.raises(ValueError, match="Unknown part_type"):
            add_part(proj, "hexagon")

    def test_part_bounds_reports_world_space_box(self):
        proj = _make_project()
        add_part(
            proj,
            "box",
            name="BoundsBox",
            params={"length": 20.0, "width": 8.0, "height": 6.0},
            position=[5.0, 10.0, 15.0],
        )
        bounds = part_bounds(proj, 0)
        assert bounds["local_bounding_box"]["min"] == {"x": 0.0, "y": 0.0, "z": 0.0}
        assert bounds["local_bounding_box"]["max"] == {"x": 20.0, "y": 8.0, "z": 6.0}
        assert bounds["world_bounding_box"]["min"] == {"x": 5.0, "y": 10.0, "z": 15.0}
        assert bounds["world_bounding_box"]["max"] == {"x": 25.0, "y": 18.0, "z": 21.0}

    def test_align_part_matches_bbox_faces(self):
        proj = _make_project()
        add_part(
            proj,
            "box",
            name="Base",
            params={"length": 20.0, "width": 10.0, "height": 6.0},
            position=[0.0, 0.0, 0.0],
        )
        add_part(
            proj,
            "box",
            name="Cap",
            params={"length": 8.0, "width": 6.0, "height": 4.0},
            position=[100.0, 50.0, 20.0],
        )

        result = align_part(
            proj,
            1,
            0,
            x="min",
            to_x="max",
            y="center",
            to_y="center",
            z="min",
            to_z="max",
        )

        assert result["placement"]["position"] == [20.0, 2.0, 6.0]
        aligned = part_bounds(proj, 1)["world_bounding_box"]
        target = part_bounds(proj, 0)["world_bounding_box"]
        assert aligned["min"]["x"] == pytest.approx(target["max"]["x"])
        assert aligned["center"]["y"] == pytest.approx(target["center"]["y"])
        assert aligned["min"]["z"] == pytest.approx(target["max"]["z"])

    def test_align_part_requires_supported_bounds(self):
        proj = _make_project()
        add_part(proj, "box", name="Base")
        add_part(
            proj,
            "cylinder",
            name="Tool",
            params={"radius": 2.0, "height": 12.0},
            position=[4.0, 4.0, -1.0],
        )
        boolean_op(proj, "cut", 0, 1, name="CutResult")
        with pytest.raises(ValueError, match="does not support bounding-box alignment"):
            align_part(proj, 2, 0, x="min", to_x="max")

    def test_remove_part(self):
        proj = _make_project()
        add_part(proj, "box", name="A")
        add_part(proj, "cylinder", name="B")
        assert len(proj["parts"]) == 2

        removed = remove_part(proj, 0)
        assert removed["name"] == "A"
        assert len(proj["parts"]) == 1
        assert proj["parts"][0]["name"] == "B"

    def test_remove_invalid_index(self):
        proj = _make_project()
        add_part(proj, "box")
        with pytest.raises(IndexError):
            remove_part(proj, 5)
        with pytest.raises(IndexError):
            remove_part(proj, -1)

    def test_list_parts(self):
        proj = _make_project()
        assert list_parts(proj) == []
        add_part(proj, "box", name="A")
        add_part(proj, "sphere", name="B")
        parts = list_parts(proj)
        assert len(parts) == 2
        assert parts[0]["name"] == "A"
        assert parts[1]["name"] == "B"

    def test_transform_part(self):
        proj = _make_project()
        add_part(proj, "box")
        updated = transform_part(proj, 0, position=[10.0, 20.0, 30.0])
        assert updated["placement"]["position"] == [10.0, 20.0, 30.0]
        # Rotation unchanged
        assert updated["placement"]["rotation"] == [0.0, 0.0, 0.0]

        updated2 = transform_part(proj, 0, rotation=[90.0, 0.0, 0.0])
        assert updated2["placement"]["rotation"] == [90.0, 0.0, 0.0]
        # Position unchanged from previous transform
        assert updated2["placement"]["position"] == [10.0, 20.0, 30.0]

    def test_boolean_cut(self):
        proj = _make_project()
        add_part(proj, "box", name="Base")
        add_part(proj, "cylinder", name="Tool")
        result = boolean_op(proj, "cut", 0, 1)

        assert result["type"] == "cut"
        assert result["params"]["base_id"] == proj["parts"][0]["id"]
        assert result["params"]["tool_id"] == proj["parts"][1]["id"]
        assert result["visible"] is True
        # Operands should be hidden
        assert proj["parts"][0]["visible"] is False
        assert proj["parts"][1]["visible"] is False
        assert len(proj["parts"]) == 3

    def test_boolean_fuse_common(self):
        proj = _make_project()
        add_part(proj, "box", name="A")
        add_part(proj, "box", name="B")

        fuse_result = boolean_op(proj, "fuse", 0, 1)
        assert fuse_result["type"] == "fuse"

        # Add two more for common test
        add_part(proj, "sphere", name="C")
        add_part(proj, "sphere", name="D")
        common_result = boolean_op(proj, "common", 3, 4)
        assert common_result["type"] == "common"

        with pytest.raises(ValueError, match="Unknown boolean op"):
            boolean_op(proj, "intersect", 0, 1)

        with pytest.raises(ValueError, match="must differ"):
            boolean_op(proj, "cut", 0, 0)


# ===========================================================================
# TestSketch
# ===========================================================================


class TestSketch:
    """Tests for the sketch module."""

    def test_create_sketch(self):
        proj = _make_project()
        sk = create_sketch(proj, name="MySketch", plane="XZ", offset=5.0)
        assert sk["name"] == "MySketch"
        assert sk["plane"] == "XZ"
        assert sk["offset"] == 5.0
        assert sk["elements"] == []
        assert sk["constraints"] == []
        assert sk["closed"] is False
        assert len(proj["sketches"]) == 1

        # Invalid plane
        with pytest.raises(ValueError, match="Invalid plane"):
            create_sketch(proj, plane="AB")

    def test_add_line(self):
        proj = _make_project()
        create_sketch(proj)
        line = add_line(proj, 0, start=[0.0, 0.0], end=[10.0, 5.0])
        assert line["type"] == "line"
        assert line["start"] == [0.0, 0.0]
        assert line["end"] == [10.0, 5.0]
        assert len(proj["sketches"][0]["elements"]) == 1

    def test_add_circle(self):
        proj = _make_project()
        create_sketch(proj)
        circle = add_circle(proj, 0, center=[1.0, 2.0], radius=8.0)
        assert circle["type"] == "circle"
        assert circle["center"] == [1.0, 2.0]
        assert circle["radius"] == 8.0

        with pytest.raises(ValueError, match="positive"):
            add_circle(proj, 0, radius=-1.0)

    def test_add_rectangle(self):
        proj = _make_project()
        create_sketch(proj)
        result = add_rectangle(proj, 0, corner=[0.0, 0.0], width=20.0, height=10.0)

        assert result["type"] == "rectangle"
        assert len(result["line_ids"]) == 4
        assert len(result["constraint_ids"]) == 4
        assert result["width"] == 20.0
        assert result["height"] == 10.0

        # 4 line elements and 4 constraints should be in the sketch
        sk = proj["sketches"][0]
        assert len(sk["elements"]) == 4
        assert len(sk["constraints"]) == 4

    def test_add_arc(self):
        proj = _make_project()
        create_sketch(proj)
        arc = add_arc(proj, 0, center=[0.0, 0.0], radius=10.0, start_angle=0.0, end_angle=90.0)
        assert arc["type"] == "arc"
        assert arc["radius"] == 10.0
        assert arc["start_angle"] == 0.0
        assert arc["end_angle"] == 90.0
        # Check computed start/end points
        assert arc["start_point"][0] == pytest.approx(10.0)
        assert arc["start_point"][1] == pytest.approx(0.0)
        assert arc["end_point"][0] == pytest.approx(0.0, abs=1e-10)
        assert arc["end_point"][1] == pytest.approx(10.0)

    def test_add_constraint_distance(self):
        proj = _make_project()
        create_sketch(proj)
        line = add_line(proj, 0, start=[0.0, 0.0], end=[10.0, 0.0])

        constraint = add_constraint(
            proj, 0, constraint_type="distance", elements=[line["id"]], value=15.0
        )
        assert constraint["type"] == "distance"
        assert constraint["value"] == 15.0
        assert constraint["elements"] == [line["id"]]

        # Missing value for dimensional constraint
        with pytest.raises(ValueError, match="requires a numeric value"):
            add_constraint(proj, 0, constraint_type="distance", elements=[line["id"]])

        # Unknown constraint type
        with pytest.raises(ValueError, match="Unknown constraint type"):
            add_constraint(proj, 0, constraint_type="magical", elements=[line["id"]])

    def test_close_sketch(self):
        proj = _make_project()
        create_sketch(proj)
        add_line(proj, 0)

        closed = close_sketch(proj, 0)
        assert closed["closed"] is True

        # Cannot add elements to a closed sketch
        with pytest.raises(ValueError, match="closed sketch"):
            add_line(proj, 0)

        # Cannot close an already closed sketch
        with pytest.raises(ValueError, match="already closed"):
            close_sketch(proj, 0)

    def test_list_and_get_sketch(self):
        proj = _make_project()
        create_sketch(proj, name="S1", plane="XY")
        create_sketch(proj, name="S2", plane="YZ")
        add_line(proj, 0)

        summaries = list_sketches(proj)
        assert len(summaries) == 2
        assert summaries[0]["name"] == "S1"
        assert summaries[0]["plane"] == "XY"
        assert summaries[0]["element_count"] == 1
        assert summaries[1]["name"] == "S2"
        assert summaries[1]["plane"] == "YZ"

        sk = get_sketch(proj, 1)
        assert sk["name"] == "S2"

        with pytest.raises(IndexError):
            get_sketch(proj, 99)


# ===========================================================================
# TestBody
# ===========================================================================


class TestBody:
    """Tests for the body module."""

    def _project_with_sketch(self):
        """Return a project with one closed sketch containing a rectangle."""
        proj = _make_project()
        create_sketch(proj, name="BaseSketch")
        add_rectangle(proj, 0, corner=[0, 0], width=10, height=10)
        close_sketch(proj, 0)
        return proj

    def test_create_body(self):
        proj = _make_project()
        body = create_body(proj, name="MyBody")
        assert body["name"] == "MyBody"
        assert body["features"] == []
        assert body["base_sketch_index"] is None
        assert len(proj["bodies"]) == 1

        # Auto-naming
        body2 = create_body(proj)
        assert body2["name"] == "Body"  # first auto "Body" is taken by none; unique check

    def test_pad(self):
        proj = self._project_with_sketch()
        create_body(proj, name="PadBody")
        feature = pad(proj, body_index=0, sketch_index=0, length=15.0, symmetric=True)
        assert feature["type"] == "pad"
        assert feature["length"] == 15.0
        assert feature["symmetric"] is True
        assert feature["reversed"] is False
        assert proj["bodies"][0]["base_sketch_index"] == 0

        with pytest.raises(ValueError, match="positive"):
            pad(proj, body_index=0, sketch_index=0, length=-5.0)

    def test_pocket(self):
        proj = self._project_with_sketch()
        create_body(proj, name="PocketBody")
        # Add a pad first so body has features
        pad(proj, body_index=0, sketch_index=0, length=20.0)

        # Create a second sketch for the pocket
        create_sketch(proj, name="PocketSketch")
        add_rectangle(proj, 1, corner=[2, 2], width=3, height=3)
        close_sketch(proj, 1)

        feature = pocket(proj, body_index=0, sketch_index=1, length=5.0)
        assert feature["type"] == "pocket"
        assert feature["length"] == 5.0

    def test_fillet(self):
        proj = self._project_with_sketch()
        create_body(proj)
        pad(proj, body_index=0, sketch_index=0, length=10.0)

        feat = fillet(proj, body_index=0, radius=2.0, edges="all")
        assert feat["type"] == "fillet"
        assert feat["radius"] == 2.0
        assert feat["edges"] == "all"

        feat2 = fillet(proj, body_index=0, radius=1.0, edges=[0, 1, 2])
        assert feat2["edges"] == [0, 1, 2]

        feat3 = fillet(proj, body_index=0, radius=1.0, edges="bottom_rim")
        assert feat3["edges"] == "bottom_rim"

        with pytest.raises(ValueError, match="positive"):
            fillet(proj, body_index=0, radius=-1.0)

        with pytest.raises(ValueError, match="Edges"):
            fillet(proj, body_index=0, radius=1.0, edges="vertical")

    def test_chamfer(self):
        proj = self._project_with_sketch()
        create_body(proj)
        pad(proj, body_index=0, sketch_index=0, length=10.0)

        feat = chamfer(proj, body_index=0, size=1.5, edges="all")
        assert feat["type"] == "chamfer"
        assert feat["size"] == 1.5
        assert feat["edges"] == "all"

        feat2 = chamfer(proj, body_index=0, size=1.0, edges="bottom_rim")
        assert feat2["edges"] == "bottom_rim"

        with pytest.raises(ValueError, match="positive"):
            chamfer(proj, body_index=0, size=0.0)

    def test_macro_binds_finishing_edges(self):
        from cli_anything.freecad.utils.freecad_macro_gen import generate_macro

        project = {
            "name": "finishing-demo",
            "parts": [],
            "boolean_ops": [],
            "bodies": [
                {
                    "id": 1,
                    "name": "MainBody",
                    "features": [
                        {
                            "id": 1,
                            "type": "additive_box",
                            "name": "Base",
                            "length": 10.0,
                            "width": 8.0,
                            "height": 6.0,
                        },
                        {
                            "id": 2,
                            "type": "chamfer",
                            "name": "RimChamfer",
                            "size": 1.0,
                            "edges": "bottom_rim",
                        },
                        {
                            "id": 3,
                            "type": "fillet",
                            "name": "AllFillet",
                            "radius": 2.5,
                            "edges": "all",
                        },
                    ],
                }
            ],
        }

        macro = generate_macro(project, "/tmp/out.fcstd", export_format="fcstd")

        assert "def _finishing_edges(shape, spec):" in macro
        assert "_finishing_edges(feat_MainBody_1_additive_box.Shape, 'bottom_rim')" in macro
        assert "_finishing_edges(feat_MainBody_2_chamfer.Shape, 'all')" in macro
        assert ".Size = 1.0" in macro
        assert ".Radius = 2.5" in macro

    def test_bayonet_groove_feature_recorded(self):
        proj = {"bodies": [], "sketches": [], "parts": []}
        create_body(proj)
        additive_cylinder(proj, body_index=0, radius=6.2, height=7.5)
        segments = [
            {"kind": "axial", "angle": 130.0, "half_width": 12.0, "z0": 8.5, "z1": 11.0},
            {"kind": "circumferential", "angle0": 130.0, "angle1": 164.0, "z0": 12.5, "z1": 13.5},
            {"kind": "axial", "angle": 164.0, "half_width": 6.0, "z0": 13.0, "z1": 15.6},
        ]
        feat = bayonet_groove(
            proj, body_index=0, segments=segments,
            wall_radius=6.2, depth=0.9, center_x=7.2, center_y=7.2, symmetry=2,
        )
        assert feat["type"] == "bayonet_groove"
        assert feat["symmetry"] == 2
        assert len(feat["segments"]) == 3
        assert feat["depth"] == 0.9

    def test_bayonet_groove_validation(self):
        proj = {"bodies": [], "sketches": [], "parts": []}
        create_body(proj)
        additive_cylinder(proj, body_index=0, radius=6.2, height=7.5)
        with pytest.raises(ValueError):
            bayonet_groove(proj, body_index=0, segments=[], wall_radius=6.2, depth=0.9)
        with pytest.raises(ValueError):
            bayonet_groove(
                proj, body_index=0,
                segments=[{"kind": "axial", "angle": 0, "z0": 0, "z1": 1}],
                wall_radius=6.2, depth=7.0,
            )

    def test_macro_binds_bayonet_swept_cut(self):
        from cli_anything.freecad.utils.freecad_macro_gen import generate_macro

        project = {
            "name": "bayonet-demo",
            "parts": [],
            "boolean_ops": [],
            "bodies": [
                {
                    "id": 1,
                    "name": "Neck",
                    "features": [
                        {
                            "id": 1,
                            "type": "additive_cylinder",
                            "name": "NeckWall",
                            "radius": 6.2,
                            "height": 7.5,
                        },
                        {
                            "id": 2,
                            "type": "bayonet_groove",
                            "name": "Bayonet",
                            "wall_radius": 6.2,
                            "depth": 0.9,
                            "center_x": 7.2,
                            "center_y": 7.2,
                            "symmetry": 2,
                            "segments": [
                                {"kind": "axial", "angle": 130.0, "half_width": 12.0, "z0": 8.5, "z1": 11.0},
                                {"kind": "circumferential", "angle0": 130.0, "angle1": 164.0, "z0": 12.5, "z1": 13.5},
                            ],
                        },
                    ],
                }
            ],
        }

        macro = generate_macro(project, "/tmp/out.fcstd", export_format="fcstd")

        # helper for the wrapped swept-cut is emitted
        assert "def _bayonet_channel_solid(" in macro
        # two symmetric channels are generated (base + 180 deg copy)
        assert macro.count("_chan = _bayonet_channel_solid(") == 2
        # groove tool solid is cut from the neck body via Part::Cut
        assert "doc.addObject('Part::Cut', 'Bayonet')" in macro
        assert ".Base = body_Neck" in macro
        # the second channel is rotated by 180 deg (symmetry=2): 130 -> 310
        assert "310.0" in macro

    def test_macro_anchors_per_segment_radius_and_depth(self):
        from cli_anything.freecad.utils.freecad_macro_gen import generate_macro

        project = {
            "name": "bayonet-local",
            "parts": [],
            "boolean_ops": [],
            "bodies": [
                {
                    "id": 1,
                    "name": "Neck",
                    "features": [
                        {"id": 1, "type": "additive_cylinder", "name": "NeckWall",
                         "radius": 6.2, "height": 7.5},
                        {
                            "id": 2, "type": "bayonet_groove", "name": "Bayonet",
                            "wall_radius": 6.26, "depth": 0.9,
                            "center_x": 0.0, "center_y": 0.0, "symmetry": 1,
                            "segments": [
                                # measured per-band sector: own ridge and depth
                                {"kind": "circumferential", "angle0": 69.0,
                                 "angle1": 157.0, "z0": 8.9, "z1": 9.4,
                                 "wall_radius": 5.31, "depth": 1.93},
                            ],
                        },
                    ],
                }
            ],
        }

        macro = generate_macro(project, "/tmp/out.fcstd", export_format="fcstd")
        # the emitted helper anchors the band to per-segment values
        assert "seg.get('wall_radius', r_outer)" in macro
        assert "seg.get('depth', depth)" in macro
        # and the measured segment values ride along in the channel spec
        assert "'wall_radius': 5.31" in macro
        assert "'depth': 1.93" in macro

    def _square_section(self, z, half, cx=0.0, cy=0.0):
        return {
            "z": z,
            "points": [
                [cx + half, cy - half], [cx + half * 0.7071, cy - half * 0.7071],
                [cx + half * 0.7071 * 1.0, cy], [cx + half, cy + half * 0.3],
                [cx + half * 0.3, cy + half], [cx, cy + half],
                [cx - half * 0.3, cy + half], [cx - half, cy + half * 0.3],
                [cx - half, cy - half * 0.3], [cx - half * 0.3, cy - half],
                [cx + half * 0.3, cy - half], [cx + half * 0.7, cy - half],
            ],
        }

    def test_additive_section_loft_feature_recorded(self):
        proj = {"bodies": [], "sketches": [], "parts": []}
        create_body(proj)
        additive_box(proj, body_index=0, length=10, width=10, height=5)
        sections = [
            self._square_section(z=5.0, half=5.0 + i)
            for i, z in enumerate([5.0, 7.5, 10.0])
        ]
        feat = additive_section_loft(proj, body_index=0, sections=sections, ruled=True)
        assert feat["type"] == "additive_section_loft"
        assert len(feat["sections"]) == 3
        assert feat["ruled"] is True
        assert feat["redrill_holes"] == []

    def test_additive_section_loft_validation(self):
        proj = {"bodies": [], "sketches": [], "parts": []}
        create_body(proj)
        additive_box(proj, body_index=0, length=10, width=10, height=5)
        # too few sections
        with pytest.raises(ValueError):
            additive_section_loft(
                proj, body_index=0,
                sections=[self._square_section(5.0, 5.0), self._square_section(7.5, 5.0)],
            )
        # mismatched point counts across sections
        sec_a = self._square_section(5.0, 5.0)
        sec_b = self._square_section(7.5, 5.0)
        sec_b["points"] = sec_b["points"][:-1]
        sec_c = self._square_section(10.0, 5.0)
        with pytest.raises(ValueError):
            additive_section_loft(proj, body_index=0, sections=[sec_a, sec_b, sec_c])

    def test_macro_binds_section_loft_fuse_and_redrill(self):
        from cli_anything.freecad.utils.freecad_macro_gen import generate_macro

        project = {
            "name": "loft-demo",
            "parts": [],
            "boolean_ops": [],
            "bodies": [
                {
                    "id": 1,
                    "name": "MainBody",
                    "features": [
                        {
                            "id": 1,
                            "type": "additive_box",
                            "name": "BasePlate",
                            "length": 10.0, "width": 10.0, "height": 2.0,
                        },
                        {
                            "id": 2,
                            "type": "additive_section_loft",
                            "name": "Lid",
                            "ruled": True,
                            "sections": [
                                {"z": 2.0, "points": [[0, 0], [5, 0], [5, 5], [0, 5]]},
                                {"z": 4.0, "points": [[1, 1], [4, 1], [4, 4], [1, 4]]},
                            ],
                            "redrill_holes": [
                                {"cx": 2.5, "cy": 2.5, "radius": 0.5, "z0": 0.0, "z1": 3.0},
                            ],
                        },
                    ],
                }
            ],
        }

        macro = generate_macro(project, "/tmp/out.fcstd", export_format="fcstd")

        assert "def _section_loft_solid(" in macro
        assert "section_loft_1_shape = _section_loft_solid(" in macro
        assert "doc.addObject('Part::Fuse', 'Lid')" in macro
        assert "obj_Lid_fuse1.Base = body_MainBody" in macro
        assert "obj_Lid_fuse1.Tool = section_loft_1" in macro
        # the redrill hole is re-cut doc-level after the fuse
        assert "Part.makeCylinder(0.5, 3.0, FreeCAD.Vector(2.5, 2.5, 0.0)" in macro
        assert "doc.addObject('Part::Cut', 'Lid_redrilled')" in macro
        assert "obj_Lid_fuse1_redrilled.Base = obj_Lid_fuse1" in macro

    def test_subtractive_section_loft_feature_recorded(self):
        proj = {"bodies": [], "sketches": [], "parts": []}
        create_body(proj)
        additive_box(proj, body_index=0, length=10, width=10, height=5)
        sections = [
            self._square_section(z=5.0, half=5.0 + i)
            for i, z in enumerate([5.0, 7.5, 10.0])
        ]
        feat = subtractive_section_loft(proj, body_index=0, sections=sections, ruled=True)
        assert feat["type"] == "subtractive_section_loft"
        assert len(feat["sections"]) == 3
        assert feat["ruled"] is True
        assert "redrill_holes" not in feat
        assert "after_cuts" not in feat

    def test_section_loft_after_cuts_flag_is_recorded_only_when_set(self):
        proj = {"bodies": [], "sketches": [], "parts": []}
        create_body(proj)
        additive_box(proj, body_index=0, length=10, width=10, height=5)
        sections = [
            self._square_section(z=5.0, half=5.0 + i)
            for i, z in enumerate([5.0, 7.5, 10.0])
        ]
        additive_default = additive_section_loft(proj, body_index=0, sections=sections)
        assert "after_cuts" not in additive_default
        additive_deferred = additive_section_loft(proj, body_index=0, sections=sections, after_cuts=True)
        assert additive_deferred["after_cuts"] is True

        subtractive_default = subtractive_section_loft(proj, body_index=0, sections=sections)
        assert "after_cuts" not in subtractive_default
        subtractive_deferred = subtractive_section_loft(
            proj, body_index=0, sections=sections, after_cuts=True
        )
        assert subtractive_deferred["after_cuts"] is True

    def test_subtractive_section_loft_validation(self):
        proj = {"bodies": [], "sketches": [], "parts": []}
        create_body(proj)
        additive_box(proj, body_index=0, length=10, width=10, height=5)
        # too few sections
        with pytest.raises(ValueError):
            subtractive_section_loft(
                proj, body_index=0,
                sections=[self._square_section(5.0, 5.0), self._square_section(7.5, 5.0)],
            )
        # mismatched point counts across sections
        sec_a = self._square_section(5.0, 5.0)
        sec_b = self._square_section(7.5, 5.0)
        sec_b["points"] = sec_b["points"][:-1]
        sec_c = self._square_section(10.0, 5.0)
        with pytest.raises(ValueError):
            subtractive_section_loft(proj, body_index=0, sections=[sec_a, sec_b, sec_c])

    def test_macro_binds_subtractive_section_loft_cut(self):
        from cli_anything.freecad.utils.freecad_macro_gen import generate_macro

        project = {
            "name": "sub-loft-demo",
            "parts": [],
            "boolean_ops": [],
            "bodies": [
                {
                    "id": 1,
                    "name": "MainBody",
                    "features": [
                        {
                            "id": 1,
                            "type": "additive_box",
                            "name": "BasePlate",
                            "length": 10.0, "width": 10.0, "height": 10.0,
                        },
                        {
                            "id": 2,
                            "type": "subtractive_section_loft",
                            "name": "Cavity",
                            "ruled": True,
                            "sections": [
                                {"z": 2.0, "points": [[1, 1], [4, 1], [4, 4], [1, 4]]},
                                {"z": 4.0, "points": [[2, 2], [3, 2], [3, 3], [2, 3]]},
                            ],
                        },
                    ],
                }
            ],
        }

        macro = generate_macro(project, "/tmp/out.fcstd", export_format="fcstd")

        assert "def _section_loft_solid(" in macro
        assert "subtractive_section_loft_1_shape = _section_loft_solid(" in macro
        assert "doc.addObject('Part::Cut', 'Cavity')" in macro
        assert "obj_Cavity_cut1.Base = body_MainBody" in macro
        assert "obj_Cavity_cut1.Tool = subtractive_section_loft_1" in macro

    def test_macro_chains_subtractive_cut_after_additive_fusion_on_same_body(self):
        """Regression for the volume campaign iteration 3 doc-level ordering
        fix: a body carrying BOTH an additive_section_loft and a
        subtractive_section_loft must chain the cut onto the fused result,
        not independently onto the raw body -- otherwise the two ops would
        each build their own disconnected top-level shape and the export
        would either miss the fused material or leave the cavity unfilled."""
        from cli_anything.freecad.utils.freecad_macro_gen import generate_macro

        project = {
            "name": "chain-demo",
            "parts": [],
            "boolean_ops": [],
            "bodies": [
                {
                    "id": 1,
                    "name": "MainBody",
                    "features": [
                        {
                            "id": 1,
                            "type": "additive_box",
                            "name": "BasePlate",
                            "length": 10.0, "width": 10.0, "height": 10.0,
                        },
                        {
                            "id": 2,
                            "type": "additive_section_loft",
                            "name": "Rim",
                            "ruled": True,
                            "sections": [
                                {"z": 10.0, "points": [[0, 0], [5, 0], [5, 5], [0, 5]]},
                                {"z": 12.0, "points": [[1, 1], [4, 1], [4, 4], [1, 4]]},
                            ],
                        },
                        {
                            "id": 3,
                            "type": "subtractive_section_loft",
                            "name": "Cavity",
                            "ruled": True,
                            "sections": [
                                {"z": 2.0, "points": [[1, 1], [4, 1], [4, 4], [1, 4]]},
                                {"z": 4.0, "points": [[2, 2], [3, 2], [3, 3], [2, 3]]},
                            ],
                        },
                    ],
                }
            ],
        }

        macro = generate_macro(project, "/tmp/out.fcstd", export_format="fcstd")

        # the additive fusion still starts from the raw body ...
        assert "obj_Rim_fuse1.Base = body_MainBody" in macro
        # ... but the subtractive cut chains onto the fused result, not the
        # raw body, so the final top-level shape includes both operations
        assert "obj_Cavity_cut1.Base = obj_Rim_fuse1" in macro

    def test_after_cuts_fuse_and_cut_run_after_the_normal_phases(self):
        """Regression for the volume campaign iteration 3 cut-then-restore
        fix: a socket tube rebuilt inside a chamber whose main_cavity is
        itself now a subtractive_section_loft (instead of an in-tree
        PartDesign::Pocket) must be added back AFTER that cavity cut runs,
        not before -- otherwise the cavity cut erases it wholesale. The
        after_cuts fuse chains onto the cavity cut's result, and the
        after_cuts bore-redrill cut chains onto that fuse."""
        from cli_anything.freecad.utils.freecad_macro_gen import generate_macro

        project = {
            "name": "after-cuts-demo",
            "parts": [],
            "boolean_ops": [],
            "bodies": [
                {
                    "id": 1,
                    "name": "MainBody",
                    "features": [
                        {
                            "id": 1,
                            "type": "additive_box",
                            "name": "BasePlate",
                            "length": 10.0, "width": 10.0, "height": 10.0,
                        },
                        {
                            "id": 2,
                            "type": "subtractive_section_loft",
                            "name": "Cavity",
                            "ruled": True,
                            "sections": [
                                {"z": 2.0, "points": [[1, 1], [4, 1], [4, 4], [1, 4]]},
                                {"z": 4.0, "points": [[2, 2], [3, 2], [3, 3], [2, 3]]},
                            ],
                        },
                        {
                            "id": 3,
                            "type": "additive_section_loft",
                            "name": "SocketRing",
                            "ruled": True,
                            "after_cuts": True,
                            "sections": [
                                {"z": 2.0, "points": [[2, 2], [3, 2], [3, 3], [2, 3]]},
                                {"z": 3.0, "points": [[2, 2], [3, 2], [3, 3], [2, 3]]},
                            ],
                        },
                        {
                            "id": 4,
                            "type": "subtractive_section_loft",
                            "name": "SocketBore",
                            "ruled": True,
                            "after_cuts": True,
                            "sections": [
                                {"z": 2.0, "points": [[2.3, 2.3], [2.7, 2.3], [2.7, 2.7], [2.3, 2.7]]},
                                {"z": 3.0, "points": [[2.3, 2.3], [2.7, 2.3], [2.7, 2.7], [2.3, 2.7]]},
                            ],
                        },
                    ],
                }
            ],
        }

        macro = generate_macro(project, "/tmp/out.fcstd", export_format="fcstd")

        # the cavity cut chains onto the raw body (nothing ran before it)
        assert "obj_Cavity_cut1.Base = body_MainBody" in macro
        # the deferred socket-ring fuse chains onto the cavity cut's result
        assert "obj_SocketRing_postfuse1.Base = obj_Cavity_cut1" in macro
        # the deferred socket-bore cut chains onto the socket-ring fuse
        assert "obj_SocketBore_postcut1.Base = obj_SocketRing_postfuse1" in macro
        # deferred fuses/cuts use their own loft variable namespace
        assert "post_cut_section_loft_1_shape = _section_loft_solid(" in macro
        assert "post_cut_subtractive_loft_1_shape = _section_loft_solid(" in macro

    def test_same_named_deferred_lofts_chain_without_self_reference(self):
        """Regression for the volume campaign iteration 5 DAG failure: several
        deferred (after_cuts) additive_section_loft features sharing one
        default display name used to reuse one Python variable, so from the
        second op on the emitted `.Base = obj_<name>` line read back the
        variable the line right above it had just rebound -- a fatal
        self-referencing Base ("the graph must be a DAG", every downstream
        shape null, no FCStd written). Each emitted op must get a unique
        Python variable, and each op's Base must reference the previous op's
        variable, never its own."""
        from cli_anything.freecad.utils.freecad_macro_gen import generate_macro

        sections = [
            {"z": 1.0, "points": [[0, 0], [1, 0], [1, 1], [0, 1]]},
            {"z": 2.0, "points": [[0, 0], [1, 0], [1, 1], [0, 1]]},
        ]
        features = [
            {
                "id": 1,
                "type": "additive_box",
                "name": "BasePlate",
                "length": 10.0, "width": 10.0, "height": 10.0,
            },
        ]
        for index in range(3):
            features.append(
                {
                    "id": 2 + index,
                    "type": "additive_section_loft",
                    "name": "Feature_additive_section_loft",
                    "ruled": True,
                    "after_cuts": True,
                    "sections": sections,
                }
            )
        project = {
            "name": "same-named-deferred-lofts",
            "parts": [],
            "boolean_ops": [],
            "bodies": [{"id": 1, "name": "MainBody", "features": features}],
        }

        macro = generate_macro(project, "/tmp/out.fcstd", export_format="fcstd")

        for line in macro.splitlines():
            stripped = line.strip()
            if ".Base = " not in stripped:
                continue
            target, _, source = stripped.partition(".Base = ")
            assert target != source, f"self-referencing Base: {stripped}"
        # the three deferred fuses chain sequentially onto each other
        assert "obj_Feature_additive_section_loft_postfuse1.Base = body_MainBody" in macro
        assert (
            "obj_Feature_additive_section_loft_postfuse2.Base = "
            "obj_Feature_additive_section_loft_postfuse1" in macro
        )
        assert (
            "obj_Feature_additive_section_loft_postfuse3.Base = "
            "obj_Feature_additive_section_loft_postfuse2" in macro
        )

    def test_deferred_ops_run_in_feature_order_not_fuses_then_cuts(self):
        """Regression for the volume campaign iteration 5 core-pin loss: the
        deferred (after_cuts) phase must run its ops in FEATURE ORDER, not
        all fuses before all cuts. The deferred ops encode explicit restore
        chains -- restore the socket tube, re-drill its bore, restore the
        slim core pin standing inside that bore. Batching the pin's fuse
        before the bore's cut lets the bore hollow the pin right back out;
        in feature order the pin is fused onto the already-drilled result
        and survives."""
        from cli_anything.freecad.utils.freecad_macro_gen import generate_macro

        ring = [
            {"z": 1.0, "points": [[0, 0], [4, 0], [4, 4], [0, 4]]},
            {"z": 5.0, "points": [[0, 0], [4, 0], [4, 4], [0, 4]]},
        ]
        bore = [
            {"z": 1.0, "points": [[1, 1], [3, 1], [3, 3], [1, 3]]},
            {"z": 5.0, "points": [[1, 1], [3, 1], [3, 3], [1, 3]]},
        ]
        pin = [
            {"z": 1.0, "points": [[1.8, 1.8], [2.2, 1.8], [2.2, 2.2], [1.8, 2.2]]},
            {"z": 5.0, "points": [[1.8, 1.8], [2.2, 1.8], [2.2, 2.2], [1.8, 2.2]]},
        ]
        project = {
            "name": "restore-chain-order",
            "parts": [],
            "boolean_ops": [],
            "bodies": [
                {
                    "id": 1,
                    "name": "MainBody",
                    "features": [
                        {
                            "id": 1,
                            "type": "additive_box",
                            "name": "BasePlate",
                            "length": 10.0, "width": 10.0, "height": 10.0,
                        },
                        {
                            "id": 2, "type": "additive_section_loft", "name": "Ring",
                            "ruled": True, "after_cuts": True, "sections": ring,
                        },
                        {
                            "id": 3, "type": "subtractive_section_loft", "name": "Bore",
                            "ruled": True, "after_cuts": True, "sections": bore,
                        },
                        {
                            "id": 4, "type": "additive_section_loft", "name": "Pin",
                            "ruled": True, "after_cuts": True, "sections": pin,
                        },
                    ],
                }
            ],
        }

        macro = generate_macro(project, "/tmp/out.fcstd", export_format="fcstd")

        # feature order preserved: ring fuse -> bore cut -> pin fuse
        assert "obj_Ring_postfuse1.Base = body_MainBody" in macro
        assert "obj_Bore_postcut1.Base = obj_Ring_postfuse1" in macro
        assert "obj_Pin_postfuse2.Base = obj_Bore_postcut1" in macro
        # the wrong (old) order would have chained the pin before the bore
        assert "obj_Pin_postfuse2.Base = obj_Ring_postfuse1" not in macro

    def test_top_rim_selector_accepted_and_bound(self):
        # top_rim is a valid semantic selector (mirror of bottom_rim at ZMax);
        # a padded oval outline exposes its whole face perimeter as one wire,
        # so the macro's _finishing_edges falls back to every in-plane edge.
        proj = self._project_with_sketch()
        create_body(proj)
        pad(proj, body_index=0, sketch_index=0, length=10.0)
        feat = fillet(proj, body_index=0, radius=1.4, edges="top_rim")
        assert feat["edges"] == "top_rim"

        from cli_anything.freecad.utils.freecad_macro_gen import generate_macro

        project = {
            "name": "top-rim-demo",
            "parts": [],
            "boolean_ops": [],
            "bodies": [
                {
                    "id": 1,
                    "name": "MainBody",
                    "features": [
                        {
                            "id": 1,
                            "type": "additive_box",
                            "name": "Base",
                            "length": 10.0,
                            "width": 8.0,
                            "height": 6.0,
                        },
                        {
                            "id": 2,
                            "type": "fillet",
                            "name": "TopRimFillet",
                            "radius": 1.4,
                            "edges": "top_rim",
                        },
                    ],
                }
            ],
        }
        macro = generate_macro(project, "/tmp/out.fcstd", export_format="fcstd")
        assert "_finishing_edges(feat_MainBody_1_additive_box.Shape, 'top_rim')" in macro
        assert "spec in ('bottom_rim', 'top_rim')" in macro
        assert "plane_z = bound_box.ZMin if spec == 'bottom_rim' else bound_box.ZMax" in macro

    def test_macro_binds_pad_profile_sketch(self):
        from cli_anything.freecad.utils.freecad_macro_gen import generate_macro

        project = {
            "name": "pad-profile-demo",
            "parts": [],
            "boolean_ops": [],
            "sketches": [
                {
                    "id": 1,
                    "name": "BaseOutline",
                    "plane": "XY",
                    "offset": 0.0,
                    "elements": [
                        {"id": 1, "type": "line", "start": [0.0, 0.0], "end": [30.0, 0.0]},
                        {"id": 2, "type": "line", "start": [30.0, 0.0], "end": [30.0, 10.0]},
                        {"id": 3, "type": "line", "start": [30.0, 10.0], "end": [0.0, 10.0]},
                        {"id": 4, "type": "line", "start": [0.0, 10.0], "end": [0.0, 0.0]},
                    ],
                    "constraints": [],
                    "closed": True,
                }
            ],
            "bodies": [
                {
                    "id": 1,
                    "name": "MainBody",
                    "features": [
                        {
                            "id": 1,
                            "type": "pad",
                            "sketch_index": 0,
                            "sketch_name": "BaseOutline",
                            "length": 16.0,
                            "symmetric": False,
                            "reversed": False,
                        }
                    ],
                }
            ],
        }

        macro = generate_macro(project, "/tmp/out.fcstd", export_format="fcstd")

        assert "newObject('Sketcher::SketchObject', 'BaseOutline')" in macro
        assert macro.count("Part.LineSegment(") == 4
        assert "FreeCAD.Vector(30.0, 10.0, 0)" in macro
        assert ".Profile = sketch_MainBody_1" in macro
        assert ".Length = 16.0" in macro
        assert "sketch_MainBody_1.Visibility = False" in macro

    def test_macro_pad_without_resolvable_sketch_keeps_previous_behavior(self):
        from cli_anything.freecad.utils.freecad_macro_gen import generate_macro

        project = {
            "name": "pad-orphan-demo",
            "parts": [],
            "boolean_ops": [],
            "sketches": [],
            "bodies": [
                {
                    "id": 1,
                    "name": "MainBody",
                    "features": [
                        {"id": 1, "type": "pad", "sketch_index": 3, "length": 5.0}
                    ],
                }
            ],
        }

        macro = generate_macro(project, "/tmp/out.fcstd", export_format="fcstd")

        assert "WARNING: Pad" in macro
        assert ".Profile =" not in macro
        assert ".Length = 5.0" in macro

    def test_macro_binds_pocket_profile_sketch(self):
        from cli_anything.freecad.utils.freecad_macro_gen import generate_macro

        project = {
            "name": "pocket-profile-demo",
            "parts": [],
            "boolean_ops": [],
            "sketches": [
                {
                    "id": 1,
                    "name": "BaseOutline",
                    "plane": "XY",
                    "offset": 0.0,
                    "elements": [
                        {"id": 1, "type": "line", "start": [0.0, 0.0], "end": [30.0, 0.0]},
                        {"id": 2, "type": "line", "start": [30.0, 0.0], "end": [30.0, 10.0]},
                        {"id": 3, "type": "line", "start": [30.0, 10.0], "end": [0.0, 10.0]},
                        {"id": 4, "type": "line", "start": [0.0, 10.0], "end": [0.0, 0.0]},
                    ],
                    "constraints": [],
                    "closed": True,
                },
                {
                    "id": 2,
                    "name": "CavityOutline",
                    "plane": "XY",
                    "offset": 8.0,
                    "elements": [
                        {"id": 1, "type": "line", "start": [2.0, 2.0], "end": [28.0, 2.0]},
                        {"id": 2, "type": "line", "start": [28.0, 2.0], "end": [28.0, 8.0]},
                        {"id": 3, "type": "line", "start": [28.0, 8.0], "end": [2.0, 8.0]},
                        {"id": 4, "type": "line", "start": [2.0, 8.0], "end": [2.0, 2.0]},
                    ],
                    "constraints": [],
                    "closed": True,
                },
            ],
            "bodies": [
                {
                    "id": 1,
                    "name": "MainBody",
                    "features": [
                        {
                            "id": 1,
                            "type": "pad",
                            "sketch_index": 0,
                            "sketch_name": "BaseOutline",
                            "length": 8.0,
                            "symmetric": False,
                            "reversed": False,
                        },
                        {
                            "id": 2,
                            "type": "pocket",
                            "sketch_index": 1,
                            "sketch_name": "CavityOutline",
                            "length": 7.0,
                            "symmetric": False,
                            "reversed": False,
                        },
                    ],
                }
            ],
        }

        macro = generate_macro(project, "/tmp/out.fcstd", export_format="fcstd")

        assert "newObject('Sketcher::SketchObject', 'CavityOutline')" in macro
        # the cavity sketch is lifted onto its plane offset along the normal
        assert "FreeCAD.Vector(0.0, 0.0, 8.0)" in macro
        assert ".Profile = sketch_MainBody_2" in macro
        assert "sketch_MainBody_2.Visibility = False" in macro
        assert ".Length = 7.0" in macro

    def test_macro_pocket_without_resolvable_sketch_keeps_previous_behavior(self):
        from cli_anything.freecad.utils.freecad_macro_gen import generate_macro

        project = {
            "name": "pocket-orphan-demo",
            "parts": [],
            "boolean_ops": [],
            "sketches": [],
            "bodies": [
                {
                    "id": 1,
                    "name": "MainBody",
                    "features": [
                        {"id": 1, "type": "pocket", "sketch_index": 3, "length": 5.0}
                    ],
                }
            ],
        }

        macro = generate_macro(project, "/tmp/out.fcstd", export_format="fcstd")

        assert "WARNING: Pocket" in macro
        assert ".Profile =" not in macro
        assert ".Length = 5.0" in macro

    def test_revolution(self):
        proj = self._project_with_sketch()
        create_body(proj)
        feat = revolution(proj, body_index=0, sketch_index=0, angle=180.0, axis="Y")
        assert feat["type"] == "revolution"
        assert feat["angle"] == 180.0
        assert feat["axis"] == "Y"
        assert feat["reversed"] is False

        with pytest.raises(ValueError, match="angle must be in"):
            revolution(proj, body_index=0, sketch_index=0, angle=0.0)

        with pytest.raises(ValueError, match="Invalid revolution axis"):
            revolution(proj, body_index=0, sketch_index=0, axis="W")

    def test_additive_primitive_placement_and_patterns(self):
        proj = _make_project()
        create_body(proj, name="TowerBody")

        base = additive_box(
            proj,
            body_index=0,
            length=12.0,
            width=10.0,
            height=18.0,
            position=[1.0, 2.0, 3.0],
            rotation=[0.0, 0.0, 15.0],
        )
        assert base["type"] == "additive_box"
        assert base["placement"]["position"] == [1.0, 2.0, 3.0]
        assert base["placement"]["rotation"] == [0.0, 0.0, 15.0]

        rib = additive_cylinder(
            proj,
            body_index=0,
            radius=2.5,
            height=6.0,
            position=[8.0, 0.0, 9.0],
        )
        assert rib["type"] == "additive_cylinder"
        assert rib["placement"]["position"] == [8.0, 0.0, 9.0]

        cone = additive_cone(
            proj,
            body_index=0,
            radius1=3.0,
            radius2=1.0,
            height=8.0,
            position=[0.0, 0.0, 18.0],
        )
        assert cone["type"] == "additive_cone"

        linear = linear_pattern(
            proj,
            body_index=0,
            direction=[0.0, 0.0, 1.0],
            length=24.0,
            occurrences=4,
        )
        assert linear["type"] == "linear_pattern"
        assert linear["direction"] == [0.0, 0.0, 1.0]
        assert linear["occurrences"] == 4

        polar = polar_pattern(
            proj,
            body_index=0,
            axis="Z",
            angle=360.0,
            occurrences=4,
        )
        assert polar["type"] == "polar_pattern"
        assert polar["axis"] == "Z"
        assert polar["occurrences"] == 4

    def test_additive_cylinder_axis_angle_placement(self):
        from cli_anything.freecad.utils.freecad_macro_gen import generate_macro

        proj = _make_project()
        create_body(proj, name="SpoutBody")
        additive_box(proj, body_index=0, length=20.0, width=20.0, height=10.0)

        spout = additive_cylinder(
            proj,
            body_index=0,
            radius=0.83,
            height=9.73,
            position=[5.0, 4.0, 3.0],
            rotation_axis=[0.969, -0.237, 0.066],
            rotation_angle=86.2,
        )
        assert spout["type"] == "additive_cylinder"
        placement = spout["placement"]
        assert placement["position"] == [5.0, 4.0, 3.0]
        # axis is normalized on the way in
        axis = placement["rotation_axis"]
        assert math.isclose(math.sqrt(sum(c * c for c in axis)), 1.0, rel_tol=1e-6)
        assert math.isclose(axis[0], 0.969, abs_tol=1e-3)
        assert placement["rotation_angle"] == 86.2

        macro = generate_macro(proj, "/tmp/spout.fcstd", export_format="fcstd")
        # the macro must tilt the cylinder via an axis-angle FreeCAD.Rotation
        assert "FreeCAD.Rotation(FreeCAD.Vector(" in macro
        assert "86.2)" in macro

    def test_additive_cylinder_axis_angle_requires_nonzero_axis(self):
        proj = _make_project()
        create_body(proj, name="ZeroAxisBody")
        additive_box(proj, body_index=0, length=10.0, width=10.0, height=10.0)
        with pytest.raises(ValueError):
            additive_cylinder(
                proj,
                body_index=0,
                radius=1.0,
                height=5.0,
                rotation_axis=[0.0, 0.0, 0.0],
                rotation_angle=30.0,
            )

    def test_subtractive_primitive_placement(self):
        proj = _make_project()
        create_body(proj, name="CutBody")
        additive_box(proj, body_index=0, length=20.0, width=20.0, height=20.0)

        cut = subtractive_box(
            proj,
            body_index=0,
            length=6.0,
            width=6.0,
            height=10.0,
            position=[0.0, 0.0, 5.0],
        )
        assert cut["type"] == "subtractive_box"
        assert cut["placement"]["position"] == [0.0, 0.0, 5.0]

    def test_list_and_get_body(self):
        proj = self._project_with_sketch()
        create_body(proj, name="B1")
        create_body(proj, name="B2")
        pad(proj, body_index=0, sketch_index=0, length=10.0)

        summaries = list_bodies(proj)
        assert len(summaries) == 2
        assert summaries[0]["name"] == "B1"
        assert summaries[0]["feature_count"] == 1
        assert summaries[1]["name"] == "B2"
        assert summaries[1]["feature_count"] == 0

        body = get_body(proj, 0)
        assert body["name"] == "B1"

        with pytest.raises(IndexError):
            get_body(proj, 99)


# ===========================================================================
# TestMaterials
# ===========================================================================


class TestMaterials:
    """Tests for the materials module."""

    def test_create_default(self):
        proj = _make_project()
        mat = create_material(proj)
        assert mat["name"] == "Material"
        assert mat["preset"] is None
        assert mat["color"] == [0.8, 0.8, 0.8, 1.0]
        assert mat["metallic"] == 0.0
        assert mat["roughness"] == 0.5
        assert mat["assigned_to"] == []
        assert len(proj["materials"]) == 1

    def test_create_from_preset(self):
        proj = _make_project()
        mat = create_material(proj, preset="steel")
        assert mat["preset"] == "steel"
        assert mat["color"] == PRESETS["steel"]["color"]
        assert mat["metallic"] == PRESETS["steel"]["metallic"]
        assert mat["roughness"] == PRESETS["steel"]["roughness"]
        # Name is derived from preset key
        assert mat["name"] == "Steel"

        with pytest.raises(ValueError, match="Unknown preset"):
            create_material(proj, preset="unobtanium")

    def test_create_with_color(self):
        proj = _make_project()
        mat = create_material(proj, name="Red", color=[1.0, 0.0, 0.0])
        # 3-component color gets alpha appended
        assert mat["color"] == [1.0, 0.0, 0.0, 1.0]

        mat2 = create_material(proj, name="SemiRed", color=[1.0, 0.0, 0.0, 0.5])
        assert mat2["color"] == [1.0, 0.0, 0.0, 0.5]

    def test_assign_to_part(self):
        proj = _make_project()
        add_part(proj, "box", name="MyBox")
        create_material(proj, name="BlueMat", color=[0.0, 0.0, 1.0])

        result = assign_material(proj, material_index=0, part_index=0)
        assert result["material"] == "BlueMat"
        assert result["part"] == "MyBox"
        # Material should track the assignment
        assert 0 in proj["materials"][0]["assigned_to"]
        # Part should reference the material
        assert proj["parts"][0]["material_index"] == 0

    def test_set_property(self):
        proj = _make_project()
        create_material(proj, name="Editable")

        set_material_property(proj, 0, "roughness", 0.9)
        assert proj["materials"][0]["roughness"] == 0.9

        set_material_property(proj, 0, "name", "Renamed")
        assert proj["materials"][0]["name"] == "Renamed"

        set_material_property(proj, 0, "color", [0.1, 0.2, 0.3, 1.0])
        assert proj["materials"][0]["color"] == [0.1, 0.2, 0.3, 1.0]

    def test_set_invalid_property(self):
        proj = _make_project()
        create_material(proj)

        with pytest.raises(ValueError):
            set_material_property(proj, 0, "nonexistent_prop", 42)

        with pytest.raises(ValueError, match="maximum"):
            set_material_property(proj, 0, "metallic", 2.0)

    def test_list_presets(self):
        presets = list_presets()
        assert isinstance(presets, list)
        assert len(presets) == len(PRESETS)
        names = {p["name"] for p in presets}
        assert "steel" in names
        assert "gold" in names
        for p in presets:
            assert "name" in p
            assert "color" in p
            assert "metallic" in p
            assert "roughness" in p


# ===========================================================================
# TestSession
# ===========================================================================


class TestSession:
    """Tests for the session module."""

    def test_status_no_project(self):
        session = Session()
        status = session.status()
        assert status["has_project"] is False
        assert status["project_path"] is None
        assert status["modified"] is False
        assert status["undo_depth"] == 0
        assert status["redo_depth"] == 0

        with pytest.raises(RuntimeError, match="No project"):
            session.get_project()

    def test_set_project(self):
        session = Session()
        proj = create_document(name="SessionTest")
        session.set_project(proj, path="/tmp/test.json")

        assert session.get_project()["name"] == "SessionTest"
        assert session.project_path == "/tmp/test.json"
        status = session.status()
        assert status["has_project"] is True
        assert status["modified"] is False

    def test_snapshot_and_undo(self):
        session = Session()
        proj = create_document(name="UndoTest")
        session.set_project(proj)

        # Take a snapshot, then mutate
        session.snapshot("before adding box")
        add_part(session.get_project(), "box", name="TempBox")
        assert len(session.get_project()["parts"]) == 1

        # Undo should restore the state before the mutation
        desc = session.undo()
        assert desc == "before adding box"
        assert len(session.get_project()["parts"]) == 0

        # Undo with empty stack returns None
        assert session.undo() is None

    def test_undo_redo_cycle(self):
        session = Session()
        proj = create_document(name="RedoTest")
        session.set_project(proj)

        # Snapshot -> mutate -> undo -> redo
        session.snapshot("add cylinder")
        add_part(session.get_project(), "cylinder", name="Cyl")
        assert len(session.get_project()["parts"]) == 1

        session.undo()
        assert len(session.get_project()["parts"]) == 0
        assert session.status()["redo_depth"] == 1

        desc = session.redo()
        assert desc == "add cylinder"
        assert len(session.get_project()["parts"]) == 1

        # Redo with empty stack returns None
        assert session.redo() is None

    def test_save_session(self, tmp_path):
        session = Session()
        proj = create_document(name="SaveTest")
        session.set_project(proj)

        filepath = str(tmp_path / "session_save.json")
        saved_path = session.save_session(path=filepath)
        assert os.path.isfile(saved_path)
        assert session.status()["modified"] is False

        # Verify the file contains valid JSON matching the project
        with open(saved_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["name"] == "SaveTest"

        # Save without path after initial save should use stored path
        session.snapshot("mark modified")
        saved_again = session.save_session()
        assert saved_again == saved_path

    def test_list_history(self):
        session = Session()
        proj = create_document(name="HistoryTest")
        session.set_project(proj)

        session.snapshot("step 1")
        add_part(session.get_project(), "box")
        session.snapshot("step 2")
        add_part(session.get_project(), "cylinder")
        session.snapshot("step 3")

        history = session.list_history()
        assert len(history) == 3
        # Newest first
        assert history[0]["description"] == "step 3"
        assert history[1]["description"] == "step 2"
        assert history[2]["description"] == "step 1"
        # Each entry has required keys
        for entry in history:
            assert "index" in entry
            assert "timestamp" in entry
            assert "description" in entry


# ===========================================================================
# TestFreeCAD11Features — New features added for FreeCAD 1.1
# ===========================================================================


class TestFreeCAD11Features:
    """Tests for FreeCAD 1.1 new features across modules."""

    # -- Body: LocalCoordinateSystem --

    def test_local_coordinate_system_default(self):
        proj = _make_project()
        body = create_body(proj, name="LCSBody")
        feat = local_coordinate_system(proj, 0)
        assert feat["type"] == "local_coordinate_system"
        assert feat["position"] == [0.0, 0.0, 0.0]
        assert feat["x_axis"] == [1.0, 0.0, 0.0]
        assert feat["y_axis"] == [0.0, 1.0, 0.0]
        assert feat["z_axis"] == [0.0, 0.0, 1.0]

    def test_local_coordinate_system_custom_axes(self):
        proj = _make_project()
        create_body(proj, name="LCSBody2")
        feat = local_coordinate_system(
            proj, 0,
            position=[10.0, 20.0, 30.0],
            x_axis=[0.0, 1.0, 0.0],
            z_axis=[1.0, 0.0, 0.0],
        )
        assert feat["position"] == [10.0, 20.0, 30.0]
        assert feat["x_axis"] == [0.0, 1.0, 0.0]

    def test_local_coordinate_system_invalid_body(self):
        proj = _make_project()
        with pytest.raises(IndexError):
            local_coordinate_system(proj, 99)

    # -- Body: Datum attachment --

    def test_datum_plane_with_attachment(self):
        proj = _make_project()
        create_body(proj, name="DatumBody")
        feat = datum_plane(proj, 0, attachment_mode="flat_face",
                           attachment_refs=["Body.Face1"])
        assert feat["attachment_mode"] == "flat_face"
        assert feat["attachment_refs"] == ["Body.Face1"]

    def test_datum_line_with_attachment(self):
        proj = _make_project()
        create_body(proj, name="DatumBody2")
        feat = datum_line(proj, 0, attachment_mode="normal_to_edge",
                          attachment_refs=["Body.Edge1"])
        assert feat["attachment_mode"] == "normal_to_edge"

    def test_datum_point_with_attachment(self):
        proj = _make_project()
        create_body(proj, name="DatumBody3")
        feat = datum_point(proj, 0, attachment_mode="translate",
                           attachment_refs=["Body.Vertex1"])
        assert feat["attachment_mode"] == "translate"

    def test_datum_invalid_attachment_mode(self):
        proj = _make_project()
        create_body(proj, name="DatumBody4")
        with pytest.raises(ValueError, match="Invalid attachment_mode"):
            datum_plane(proj, 0, attachment_mode="nonexistent_mode")

    # -- Body: Hole Whitworth threads --

    def test_hole_whitworth_bsw(self):
        proj = _make_project()
        create_body(proj, name="HoleBody")
        sk = create_sketch(proj)
        add_line(proj, 0, [0, 0], [10, 0])
        close_sketch(proj, 0)
        pad(proj, 0, sketch_index=0, length=10.0)
        feat = hole_feature(proj, 0, sketch_index=0, diameter=6.0, depth=10.0,
                            threaded=True, thread_standard="BSW")
        assert feat["thread_standard"] == "BSW"

    def test_hole_npt_auto_taper(self):
        proj = _make_project()
        create_body(proj, name="HoleBody2")
        sk = create_sketch(proj)
        add_line(proj, 0, [0, 0], [10, 0])
        close_sketch(proj, 0)
        pad(proj, 0, sketch_index=0, length=10.0)
        feat = hole_feature(proj, 0, sketch_index=0, diameter=6.0, depth=10.0,
                            threaded=True, thread_standard="NPT", tapered=True)
        assert feat["tapered"] is True
        assert abs(feat["taper_angle"] - 1.7899) < 0.001

    def test_hole_invalid_thread_standard(self):
        proj = _make_project()
        create_body(proj, name="HoleBody3")
        sk = create_sketch(proj)
        add_line(proj, 0, [0, 0], [10, 0])
        close_sketch(proj, 0)
        pad(proj, 0, sketch_index=0, length=10.0)
        with pytest.raises(ValueError, match="Invalid thread_standard"):
            hole_feature(proj, 0, sketch_index=0, diameter=6.0, depth=10.0,
                         thread_standard="INVALID")

    # -- Body: Toggle freeze --

    def test_toggle_freeze(self):
        proj = _make_project()
        create_body(proj, name="FreezeBody")
        create_sketch(proj)
        add_line(proj, 0, [0, 0], [10, 0])
        close_sketch(proj, 0)
        pad(proj, 0, sketch_index=0, length=5.0)
        feat = toggle_freeze(proj, 0, 0)
        assert feat["frozen"] is True
        feat2 = toggle_freeze(proj, 0, 0)
        assert feat2["frozen"] is False

    def test_toggle_freeze_invalid_index(self):
        proj = _make_project()
        create_body(proj, name="FreezeBody2")
        with pytest.raises(IndexError):
            toggle_freeze(proj, 0, 99)


class TestPreview:
    @staticmethod
    def _fake_bundle(tmp_path, bundle_id):
        bundle_dir = tmp_path / bundle_id
        artifacts_dir = bundle_dir / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        for artifact_id in ("hero", "front", "top", "right"):
            (artifacts_dir / f"{artifact_id}.png").write_bytes(b"\x89PNG\r\n\x1a\npreview")
        summary_path = bundle_dir / "summary.json"
        summary_path.write_text(
            json.dumps(
                {
                    "headline": "FreeCAD quick preview",
                    "facts": {"views": 4, "units": "mm"},
                }
            )
        )
        manifest_path = bundle_dir / "manifest.json"
        manifest_path.write_text(json.dumps({"bundle_id": bundle_id, "status": "ok"}))
        return {
            "bundle_id": bundle_id,
            "status": "ok",
            "_bundle_dir": str(bundle_dir),
            "_manifest_path": str(manifest_path),
            "_summary_path": str(summary_path),
            "cached": False,
        }

    def test_list_recipes(self):
        recipes = preview_mod.list_recipes()
        assert recipes
        assert recipes[0]["name"] == "quick"

    def test_capture_bundle(self, tmp_path, monkeypatch):
        sess = Session()
        proj = create_document(name="PreviewDoc")
        add_part(proj, "box", name="HeroBox")
        sess.set_project(proj, str(tmp_path / "project.json"))

        def fake_capture(script_content, timeout=120, gui_required=False, env=None):
            marker = "artifacts/"
            for line in script_content.splitlines():
                if marker in line and ".png" in line:
                    path = line.split("'")[3]
                    Path(path).parent.mkdir(parents=True, exist_ok=True)
                    Path(path).write_bytes(b"\x89PNG\r\n\x1a\npreview")
            return {"returncode": 0, "stdout": "", "stderr": ""}

        monkeypatch.setattr(freecad_backend, "run_macro_content", fake_capture)

        manifest = preview_mod.capture(sess, root_dir=str(tmp_path))
        assert manifest["software"] == "freecad"
        assert manifest["recipe"] == "quick"
        assert any(item["role"] == "hero" for item in manifest["artifacts"])
        assert len(manifest["artifacts"]) >= 4

    def test_latest_bundle(self, tmp_path, monkeypatch):
        sess = Session()
        proj = create_document(name="PreviewDoc")
        add_part(proj, "box", name="HeroBox")
        sess.set_project(proj)

        def fake_capture(script_content, timeout=120, gui_required=False, env=None):
            for line in script_content.splitlines():
                if "artifacts/" in line and ".png" in line:
                    path = line.split("'")[3]
                    Path(path).parent.mkdir(parents=True, exist_ok=True)
                    Path(path).write_bytes(b"\x89PNG\r\n\x1a\npreview")
            return {"returncode": 0, "stdout": "", "stderr": ""}

        monkeypatch.setattr(freecad_backend, "run_macro_content", fake_capture)

        created = preview_mod.capture(sess, root_dir=str(tmp_path))
        latest = preview_mod.latest(root_dir=str(tmp_path))
        assert latest["bundle_id"] == created["bundle_id"]

    def test_live_start_publishes_session(self, tmp_path, monkeypatch):
        sess = Session()
        proj = create_document(name="LivePreviewDoc")
        add_part(proj, "box", name="HeroBox")
        project_path = tmp_path / "live-demo.json"
        save_document(proj, str(project_path))
        sess.set_project(proj, str(project_path))
        bundle_manifest = self._fake_bundle(tmp_path / "bundles", "bundle-a")

        def fake_capture(*args, **kwargs):
            return dict(bundle_manifest)

        monkeypatch.setattr(preview_mod, "capture", fake_capture)

        live = preview_mod.live_start(
            sess,
            root_dir=str(tmp_path),
            refresh_hint_ms=900,
            live_mode="poll",
            source_poll_ms=420,
        )
        assert live["protocol_version"] == preview_mod.LIVE_PROTOCOL_VERSION
        assert live["current_bundle_id"] == "bundle-a"
        assert live["refresh_hint_ms"] == 900
        assert live["live_mode"] == "poll"
        assert live["source_poll_ms"] == 420
        assert live["source_state"]["project_path"] == str(project_path)
        assert live["source_state"]["last_rendered_fingerprint"].startswith("sha256:")
        assert Path(live["_session_path"]).is_file()
        assert Path(live["_trajectory_path"]).is_file()
        assert (Path(live["_session_dir"]) / "current" / "manifest.json").is_file()
        trajectory = json.loads(Path(live["_trajectory_path"]).read_text(encoding="utf-8"))
        assert trajectory["step_count"] == 1
        assert trajectory["steps"][0]["bundle_id"] == "bundle-a"
        assert live["trajectory_step_count"] == 1

    def test_live_push_updates_history(self, tmp_path, monkeypatch):
        sess = Session()
        proj = create_document(name="LivePreviewDoc")
        add_part(proj, "box", name="HeroBox")
        project_path = tmp_path / "live-demo.json"
        save_document(proj, str(project_path))
        sess.set_project(proj, str(project_path))
        bundle_a = self._fake_bundle(tmp_path / "bundles", "bundle-a")
        bundle_b = self._fake_bundle(tmp_path / "bundles", "bundle-b")
        manifests = [dict(bundle_a), dict(bundle_b)]

        def fake_capture(*args, **kwargs):
            return manifests.pop(0)

        monkeypatch.setattr(preview_mod, "capture", fake_capture)

        started = preview_mod.live_start(sess, root_dir=str(tmp_path))
        pushed = preview_mod.live_push(sess, root_dir=str(tmp_path))
        assert started["current_bundle_id"] == "bundle-a"
        assert pushed["current_bundle_id"] == "bundle-b"
        assert pushed["history"][0]["bundle_id"] == "bundle-b"
        assert pushed["history"][1]["bundle_id"] == "bundle-a"
        trajectory = json.loads(Path(pushed["_trajectory_path"]).read_text(encoding="utf-8"))
        assert trajectory["step_count"] == 2
        assert [step["bundle_id"] for step in trajectory["steps"]] == ["bundle-a", "bundle-b"]
        assert pushed["current_step_id"] == "step-0002"

    def test_live_status_includes_trajectory_summary(self, tmp_path, monkeypatch):
        sess = Session()
        proj = create_document(name="LivePreviewDoc")
        add_part(proj, "box", name="HeroBox")
        project_path = tmp_path / "live-demo.json"
        save_document(proj, str(project_path))
        sess.set_project(proj, str(project_path))
        bundle_manifest = self._fake_bundle(tmp_path / "bundles", "bundle-a")

        monkeypatch.setattr(preview_mod, "capture", lambda *args, **kwargs: dict(bundle_manifest))

        preview_mod.live_start(sess, root_dir=str(tmp_path), live_mode="manual")
        status = preview_mod.live_status(sess, root_dir=str(tmp_path))
        summary = status["trajectory_summary"]
        assert summary["step_count"] == 1
        assert summary["latest_bundle_id"] == "bundle-a"
        assert summary["latest_publish_reason"] == "live-start"
        assert summary["recent_steps"][0]["step_id"] == "step-0001"

    def test_live_push_records_publish_time_when_bundle_is_reused(self, tmp_path, monkeypatch):
        sess = Session()
        proj = create_document(name="LivePreviewDoc")
        add_part(proj, "box", name="HeroBox")
        project_path = tmp_path / "live-demo.json"
        save_document(proj, str(project_path))
        sess.set_project(proj, str(project_path))
        bundle_manifest = self._fake_bundle(tmp_path / "bundles", "bundle-a")
        bundle_manifest["created_at"] = "2025-01-01T00:00:00Z"
        publish_times = [
            "2026-04-23T10:00:00Z",
            "2026-04-23T10:05:00Z",
        ]

        monkeypatch.setattr(preview_mod, "capture", lambda *args, **kwargs: dict(bundle_manifest))
        monkeypatch.setattr(preview_mod, "_now_iso", lambda: publish_times.pop(0))

        preview_mod.live_start(sess, root_dir=str(tmp_path), live_mode="manual")
        pushed = preview_mod.live_push(sess, root_dir=str(tmp_path))
        trajectory = json.loads(Path(pushed["_trajectory_path"]).read_text(encoding="utf-8"))

        assert len(pushed["history"]) == 1
        assert [step["bundle_id"] for step in trajectory["steps"]] == ["bundle-a", "bundle-a"]
        assert [step["command_finished_at"] for step in trajectory["steps"]] == [
            "2026-04-23T10:00:00Z",
            "2026-04-23T10:05:00Z",
        ]
        assert all(step["created_at"] == "2025-01-01T00:00:00Z" for step in trajectory["steps"])

    def test_live_stop_marks_session_stopped(self, tmp_path, monkeypatch):
        sess = Session()
        proj = create_document(name="LivePreviewDoc")
        add_part(proj, "box", name="HeroBox")
        project_path = tmp_path / "live-demo.json"
        save_document(proj, str(project_path))
        sess.set_project(proj, str(project_path))
        bundle_manifest = self._fake_bundle(tmp_path / "bundles", "bundle-a")

        def fake_capture(*args, **kwargs):
            return dict(bundle_manifest)

        monkeypatch.setattr(preview_mod, "capture", fake_capture)

        preview_mod.live_start(sess, root_dir=str(tmp_path))
        stopped = preview_mod.live_stop(sess, root_dir=str(tmp_path))
        assert stopped["status"] == "stopped"
        assert "stopped_at" in stopped

    def test_live_session_name_is_stable_for_same_project_path(self, tmp_path):
        project_path = tmp_path / "stable-demo.json"
        proj = create_document(name="StablePreviewDoc")
        save_document(proj, str(project_path))

        session_a = Session()
        session_a.set_project(proj, str(project_path))

        session_b = Session()
        session_b.set_project(open_document(str(project_path)), str(project_path))

        name_a = preview_mod._live_session_name(session_a, "quick")
        name_b = preview_mod._live_session_name(session_b, "quick")
        assert name_a == name_b

    def test_project_fingerprint_is_stable_across_sessions_for_saved_project(self, tmp_path):
        project_path = tmp_path / "stable-project.json"
        proj = create_document(name="StablePreviewDoc")
        save_document(proj, str(project_path))

        session_a = Session()
        session_a.set_project(proj, str(project_path))

        session_b = Session()
        session_b.set_project(open_document(str(project_path)), str(project_path))

        assert preview_mod._project_fingerprint(session_a) == preview_mod._project_fingerprint(session_b)

    def test_poll_live_session_once_captures_after_source_change(self, tmp_path, monkeypatch):
        sess = Session()
        proj = create_document(name="PollingPreviewDoc")
        add_part(proj, "box", name="HeroBox")
        project_path = tmp_path / "polling-demo.json"
        save_document(proj, str(project_path))
        sess.set_project(proj, str(project_path))
        bundle_a = self._fake_bundle(tmp_path / "bundles", "bundle-a")
        bundle_b = self._fake_bundle(tmp_path / "bundles", "bundle-b")
        manifests = [dict(bundle_a), dict(bundle_b)]

        def fake_capture(*args, **kwargs):
            return manifests.pop(0)

        monkeypatch.setattr(preview_mod, "capture", fake_capture)

        started = preview_mod.live_start(
            sess,
            root_dir=str(tmp_path),
            live_mode="poll",
            source_poll_ms=500,
        )
        started_session_dir = Path(started["_session_dir"])

        updated = open_document(str(project_path))
        add_part(updated, "cylinder", name="ChangedPart")
        save_document(updated, str(project_path))

        result = preview_mod.poll_live_session_once(str(started_session_dir))
        payload = json.loads((started_session_dir / "session.json").read_text(encoding="utf-8"))
        trajectory = json.loads((started_session_dir / "trajectory.json").read_text(encoding="utf-8"))
        assert result["action"] == "captured"
        assert result["bundle_id"] == "bundle-b"
        assert payload["bundle_count"] >= 2
        assert payload["source_state"]["last_publish_reason"] == "auto-poll"
        assert trajectory["step_count"] == 2
        assert trajectory["steps"][-1]["publish_reason"] == "auto-poll"

    def test_poll_live_session_once_exits_for_manual_mode(self, tmp_path, monkeypatch):
        sess = Session()
        proj = create_document(name="ManualPreviewDoc")
        add_part(proj, "box", name="HeroBox")
        project_path = tmp_path / "manual-demo.json"
        save_document(proj, str(project_path))
        sess.set_project(proj, str(project_path))
        bundle_manifest = self._fake_bundle(tmp_path / "bundles", "bundle-a")

        def fake_capture(*args, **kwargs):
            return dict(bundle_manifest)

        monkeypatch.setattr(preview_mod, "capture", fake_capture)

        started = preview_mod.live_start(sess, root_dir=str(tmp_path), live_mode="manual")
        result = preview_mod.poll_live_session_once(started["_session_dir"])
        assert result["action"] == "exit"
        assert result["reason"] == "mode:manual"


class TestMotion:
    """Tests for motion sequencing and interpolation."""

    def test_create_motion_defaults(self):
        proj = _make_project()
        motion = motion_mod.create_motion(proj, name="Drive")
        assert motion["name"] == "Drive"
        assert motion["duration"] == 2.0
        assert motion["fps"] == 24
        assert motion["camera"] == "hero"
        assert motion["fit_mode"] == "initial"
        assert motion["tracks"] == []
        assert len(proj["motions"]) == 1

    def test_add_keyframes_and_sample_interpolates(self):
        proj = _make_project()
        add_part(proj, "box", name="RoverBody", position=[0.0, 0.0, 0.0])
        motion_mod.create_motion(proj, name="Drive", duration=2.0, fps=10)
        motion_mod.add_keyframe(
            proj,
            0,
            target_kind="part",
            target_index=0,
            time_value=0.0,
            position=[0.0, 0.0, 0.0],
            rotation=[0.0, 0.0, 0.0],
        )
        motion_mod.add_keyframe(
            proj,
            0,
            target_kind="part",
            target_index=0,
            time_value=2.0,
            position=[20.0, 10.0, 0.0],
            rotation=[0.0, 0.0, 90.0],
        )

        sample = motion_mod.sample_motion(proj, 0, 1.0)
        assert sample["time"] == 1.0
        assert len(sample["placements"]) == 1
        placement = sample["placements"][0]
        assert placement["position"] == [10.0, 5.0, 0.0]
        assert placement["rotation"] == [0.0, 0.0, 45.0]

    def test_apply_motion_returns_project_copy(self):
        proj = _make_project()
        add_part(proj, "box", name="Wheel", position=[1.0, 2.0, 3.0], rotation=[0.0, 0.0, 0.0])
        motion_mod.create_motion(proj, duration=1.0, fps=5)
        motion_mod.add_keyframe(proj, 0, target_kind="part", target_index=0, time_value=0.0)
        motion_mod.add_keyframe(
            proj,
            0,
            target_kind="part",
            target_index=0,
            time_value=1.0,
            position=[11.0, 2.0, 3.0],
            rotation=[0.0, 30.0, 0.0],
        )

        animated = motion_mod.apply_motion(proj, 0, 0.5)
        assert animated is not proj
        assert animated["parts"][0]["placement"]["position"] == [6.0, 2.0, 3.0]
        assert animated["parts"][0]["placement"]["rotation"] == [0.0, 15.0, 0.0]
        assert proj["parts"][0]["placement"]["position"] == [1.0, 2.0, 3.0]

    def test_render_video_requires_supported_extension(self, tmp_path):
        proj = _make_project()
        add_part(proj, "box", name="Box")
        motion_mod.create_motion(proj, duration=1.0, fps=5)
        motion_mod.add_keyframe(proj, 0, target_kind="part", target_index=0, time_value=0.0)
        with pytest.raises(ValueError, match="supports .mp4, .webm, and .gif"):
            motion_mod.render_video(proj, 0, str(tmp_path / "bad.avi"))


class TestFreeCADBackend:
    """Tests for backend command selection and wrapper handling."""

    def test_detects_gui_wrapper_script(self, tmp_path):
        wrapper = _make_wrapper_script(tmp_path / "freecad-wrapper")
        assert freecad_backend._is_gui_wrapper_script(str(wrapper)) is True

    def test_macro_command_forces_gui_branch_for_wrapper(self, tmp_path):
        wrapper = _make_wrapper_script(tmp_path / "freecad-wrapper")
        script_path = tmp_path / "macro.py"
        script_path.write_text("print('ok')\n", encoding="utf-8")

        argv = freecad_backend._macro_command(str(wrapper), str(script_path), gui_required=True)
        assert argv == [str(wrapper), "freecad", str(script_path)]

    def test_run_macro_uses_wrapper_gui_override(self, tmp_path, monkeypatch):
        wrapper = _make_wrapper_script(tmp_path / "freecad-wrapper")
        script_path = tmp_path / "macro.py"
        script_path.write_text("print('ok')\n", encoding="utf-8")
        captured = {}

        monkeypatch.setattr(freecad_backend, "find_freecad", lambda gui_required=False: str(wrapper))

        def fake_run(args, *, timeout=120, check=False, env=None):
            captured["args"] = args
            captured["timeout"] = timeout
            captured["env"] = env
            return {"command": " ".join(args), "returncode": 0, "stdout": "", "stderr": "", "ok": True}

        monkeypatch.setattr(freecad_backend, "_run", fake_run)

        result = freecad_backend.run_macro(str(script_path), timeout=55, gui_required=True, env={"QT_QPA_PLATFORM": "offscreen"})

        assert result["returncode"] == 0
        assert captured["args"] == [str(wrapper), "freecad", str(script_path.resolve())]
        assert captured["timeout"] == 55
        assert captured["env"] == {"QT_QPA_PLATFORM": "offscreen"}
