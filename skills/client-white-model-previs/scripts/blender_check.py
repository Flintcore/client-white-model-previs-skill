#!/usr/bin/env python3
"""Read-only, fail-closed Blender checks for a client-white-model-job.v1 job.

Run with the saved candidate loaded, not an unsaved interactive scene:
  blender --background --disable-autoexec candidate.blend --python-exit-code 1 \
    --python blender_check.py -- --job job.json --report blender-qa.json

The script evaluates every frame, never saves or renders, and never edits
preferences. A machine-check pass is not visual/client approval. Tolerances are
engineering defaults, not numerical limits invented on behalf of the client.
Foot contact uses explicit evaluated shin-cap vertices against the evaluated
support meshes. An ankle control, a floor Z constant, or a gait label is not a
substitute for actual mesh contact.
"""
from __future__ import annotations

import argparse
from collections import Counter
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import re
import struct
import sys
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import validate_render_contract, raytracing_matches, delivery_dimensions

import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree

PARTS = (
    "head", "torso", "upper_arm_l", "forearm_l", "upper_arm_r", "forearm_r",
    "thigh_l", "shin_l", "thigh_r", "shin_r",
)
DEFAULT_THRESHOLDS = {
    "contact_gap_m": 0.015,
    "penetration_m": 0.005,
    "stance_drift_m": 0.03,
    "max_joint_step_m": 0.12,
    "max_joint_angle_deg": 18.0,
    "rigid_edge_drift_m": 0.0001,
}
SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp-" + str(os.getpid()))
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def relative_input(root, value):
    """Input identity is local to the job bundle; path traversal is rejected."""
    if not isinstance(value, str) or not value or Path(value).is_absolute() or re.match(r"^[A-Za-z]:", value):
        raise ValueError("A nonempty job-relative input path is required")
    result = (root / value).resolve()
    if root.resolve() not in result.parents:
        raise ValueError("Input path leaves the job bundle")
    if not result.is_file():
        raise ValueError("Missing job input: " + value)
    return result


def action_curves(action):
    """Legacy actions and Blender 4.4+/5.x layered actions, without duplicates."""
    seen = set()
    for curve in getattr(action, "fcurves", ()):
        seen.add(curve.as_pointer())
        yield curve
    for layer in getattr(action, "layers", ()):
        for strip in layer.strips:
            for bag in getattr(strip, "channelbags", ()):
                for curve in bag.fcurves:
                    if curve.as_pointer() not in seen:
                        seen.add(curve.as_pointer())
                        yield curve


def actions_for(objects):
    seen = set()
    for obj in objects:
        for owner in (obj, obj.data):
            animation = getattr(owner, "animation_data", None)
            if not animation:
                continue
            candidates = [animation.action] if animation.action else []
            candidates += [strip.action for track in animation.nla_tracks for strip in track.strips if strip.action]
            for action in candidates:
                if action.as_pointer() not in seen:
                    seen.add(action.as_pointer())
                    yield action


def image_texture_nodes(tree, visited=None):
    visited = set() if visited is None else visited
    if not tree or tree.as_pointer() in visited:
        return []
    visited.add(tree.as_pointer())
    found = []
    for node in tree.nodes:
        if node.type in {"TEX_IMAGE", "TEX_ENVIRONMENT"}:
            found.append(node.name)
        if node.type == "GROUP":
            found.extend(image_texture_nodes(node.node_tree, visited))
    return found


def dependencies(scene):
    rows = []
    for kind, data in (("image", bpy.data.images), ("movieclip", bpy.data.movieclips),
                       ("sound", bpy.data.sounds), ("library", bpy.data.libraries),
                       ("font", bpy.data.fonts), ("volume", bpy.data.volumes),
                       ("cache", getattr(bpy.data, "cache_files", ()))):
        for item in data:
            path = getattr(item, "filepath", "")
            if not path or path == "<builtin>":
                continue
            if kind == "image" and item.source not in {"FILE", "MOVIE", "SEQUENCE"}:
                continue
            packed = bool(getattr(item, "packed_file", None) or getattr(item, "packed_files", ()))
            resolved = Path(bpy.path.abspath(path, library=getattr(item, "library", None)))
            rows.append({"kind": kind, "name": item.name, "path": path,
                         "packed": packed, "relative": path.startswith("//"),
                         "exists": packed or resolved.is_file(),
                         "portable": packed or path.startswith("//")})
    if scene.sequence_editor:
        strips = getattr(scene.sequence_editor, "strips_all", getattr(scene.sequence_editor, "sequences_all", ()))
        for strip in strips:
            path = getattr(strip, "filepath", "")
            paths = [path] if path else []
            directory = getattr(strip, "directory", "")
            if directory:
                paths.extend(directory + item.filename for item in getattr(strip, "elements", ()))
            for path in paths:
                rows.append({"kind": "sequence", "name": strip.name, "path": path,
                             "packed": False, "relative": path.startswith("//"),
                             "exists": Path(bpy.path.abspath(path)).is_file(),
                             "portable": path.startswith("//")})
    # Blender's own dependency walker catches referenced paths not surfaced by
    # the named datablock/sequence collections above. Unknown resource kinds
    # remain subject to the same final-bundle restriction; they are not
    # silently treated as packed or ignored.
    seen_paths = {row["path"] for row in rows}
    for path in bpy.utils.blend_paths(absolute=False, packed=True, local=False):
        if not path or path == "<builtin>" or path in seen_paths:
            continue
        seen_paths.add(path)
        rows.append({"kind": "other_blender_dependency", "name": "external resource", "path": path,
                     "packed": False, "relative": path.startswith("//"),
                     "exists": Path(bpy.path.abspath(path)).is_file(), "portable": path.startswith("//")})
    return rows


def renderable_members(scene):
    """Objects reachable through at least one enabled render-layer path.

    Checking only obj.users_collection misses a hidden parent collection and
    misclassifies an object linked to both hidden and visible collections.
    View-layer exclusions also affect rendering even with hide_render=False.
    """
    names = set()

    def visit(layer_collection, inherited):
        hidden = inherited or layer_collection.exclude or layer_collection.collection.hide_render
        if not hidden:
            names.update(obj.name for obj in layer_collection.collection.objects)
        for child in layer_collection.children:
            visit(child, hidden)

    for layer in scene.view_layers:
        if layer.use:
            visit(layer.layer_collection, False)
    return names


def mesh_world(obj, depsgraph):
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        points = [evaluated.matrix_world @ vertex.co for vertex in mesh.vertices]
        edges = [(int(edge.vertices[0]), int(edge.vertices[1])) for edge in mesh.edges]
        polygons = [list(poly.vertices) for poly in mesh.polygons]
        return points, edges, polygons
    finally:
        evaluated.to_mesh_clear()


def mesh_shape(points, edges, polygons):
    if not points or not edges or not polygons:
        return {"volumetric": False, "closed": False, "outward": False}
    ranges = [max(p[i] for p in points) - min(p[i] for p in points) for i in range(3)]
    edge_counts = Counter()
    volume = 0.0
    for poly in polygons:
        for i, a in enumerate(poly):
            b = poly[(i + 1) % len(poly)]
            edge_counts[tuple(sorted((a, b)))] += 1
        for index in range(1, len(poly) - 1):
            volume += points[poly[0]].dot(points[poly[index]].cross(points[poly[index + 1]])) / 6.0
    # Signed volume and closed topology reject planes/cards, including rotated
    # planes whose world AABB happens to have three nonzero dimensions.
    closed = bool(edge_counts) and all(count == 2 for count in edge_counts.values())
    return {"volumetric": abs(volume) > 1e-10 and min(ranges) > 1e-6,
            "closed": closed, "outward": volume > 1e-10, "signed_volume_m3": volume}


def support_bvhs(objects, depsgraph):
    surfaces = []
    highest = -math.inf
    for obj in objects:
        points, _, polygons = mesh_world(obj, depsgraph)
        if points and polygons:
            highest = max(highest, max(point.z for point in points))
            surfaces.append((obj.name, BVHTree.FromPolygons(points, polygons, all_triangles=False)))
    return surfaces, highest


def contact_for(point, surfaces, highest):
    if not math.isfinite(highest):
        return None
    hits = []
    for name, bvh in surfaces:
        origin = Vector((point.x, point.y, max(highest, point.z) + 1.0))
        hit, normal, _, _ = bvh.ray_cast(origin, Vector((0, 0, -1)), 10000.0)
        if hit is not None and normal.z > 0.05:
            hits.append({"gap_m": float(point.z - hit.z), "support": name, "height_m": float(hit.z)})
    return max(hits, key=lambda row: row["height_m"]) if hits else None


def png_information(path):
    """Check dimensions and every PNG chunk CRC, rather than trusting suffixes."""
    with Path(path).open("rb") as handle:
        if handle.read(8) != b"\x89PNG\r\n\x1a\n":
            raise ValueError("Not a PNG")
        width = height = None
        saw_data = saw_end = False
        while True:
            header = handle.read(8)
            if len(header) != 8:
                raise ValueError("Truncated PNG")
            size, kind = struct.unpack(">I4s", header)
            if size > 256 * 1024 * 1024:
                raise ValueError("Invalid PNG chunk length")
            payload = handle.read(size)
            crc_raw = handle.read(4)
            if len(payload) != size or len(crc_raw) != 4:
                raise ValueError("Truncated PNG chunk")
            if (zlib.crc32(kind + payload) & 0xFFFFFFFF) != struct.unpack(">I", crc_raw)[0]:
                raise ValueError("PNG CRC mismatch")
            if kind == b"IHDR":
                if width is not None or size != 13:
                    raise ValueError("Invalid PNG IHDR")
                width, height = struct.unpack(">II", payload[:8])
            saw_data |= kind == b"IDAT"
            if kind == b"IEND":
                saw_end = size == 0
                if handle.read(1):
                    raise ValueError("Unexpected PNG trailing data")
                break
        if not (width and height and saw_data and saw_end):
            raise ValueError("Incomplete PNG")
    return {"path": Path(path).name, "width": width, "height": height, "sha256": sha256(path)}


class Checker:
    def __init__(self, job_path, report_path, renders=None):
        self.job_path = Path(job_path).resolve()
        self.root = self.job_path.parent
        self.job = read_json(self.job_path)
        self.report_path = Path(report_path).resolve()
        self.renders = Path(renders).resolve() if renders else None
        self.scene = bpy.context.scene
        self.before_hash = sha256(bpy.data.filepath) if bpy.data.filepath and Path(bpy.data.filepath).is_file() else None
        self.report = {
            "schema": "client-white-model-blender-qa.v1", "job_id": self.job.get("job_id"),
            "standards_sha256": self.job.get("standards_sha256"), "blend_sha256": self.before_hash,
            "job_sha256": sha256(self.job_path), "blender_version": bpy.app.version_string,
            "passed": False, "checks": [], "engineering_thresholds_not_client": True,
            "visual_approval": False, "scope": "Machine checks only; camera, acting, occlusion, lighting and source equality need separate visual evidence",
        }
        self.thresholds = dict(DEFAULT_THRESHOLDS)
        supplied = self.job.get("engineering_thresholds", {})
        if not isinstance(supplied, dict):
            raise ValueError("engineering_thresholds must be an object")
        self.thresholds.update(supplied)
        valid = all(isinstance(self.thresholds[key], (int, float)) and not isinstance(self.thresholds[key], bool)
                    and math.isfinite(self.thresholds[key]) and self.thresholds[key] >= 0 for key in DEFAULT_THRESHOLDS)
        self.add("engineering_thresholds", valid, self.thresholds)
        self.report["engineering_thresholds"] = self.thresholds

    def add(self, name, passed, details):
        self.report["checks"].append({"name": name, "passed": bool(passed), "details": details})

    def inputs(self):
        self.add("job_schema", self.job.get("schema") == "client-white-model-job.v1" and bool(self.job.get("job_id")),
                 {"schema": self.job.get("schema"), "job_id": self.job.get("job_id")})
        self.add("saved_blend", bool(self.before_hash), {"saved": bool(self.before_hash)})
        for key in ("source", "template"):
            spec = self.job.get(key, {})
            try:
                path = relative_input(self.root, spec.get("path"))
                expected = spec.get("sha256", "")
                actual = sha256(path)
                valid = bool(SHA_PATTERN.fullmatch(str(expected))) and actual == expected.lower()
                self.add(key + "_identity", valid, {"path": spec.get("path"), "expected_sha256": expected, "actual_sha256": actual})
            except (OSError, ValueError, TypeError) as error:
                self.add(key + "_identity", False, {"error": str(error)})
        self.add("template_level", self.job.get("template", {}).get("level") == "L3", {"required": "L3"})
        # Only the skill's immutable bundled standards are accepted. A job
        # cannot replace them with an easier alternate standards document.
        try:
            standards_path = Path(__file__).resolve().parents[1] / "references" / "standards.json"
            if not standards_path.is_file():
                raise ValueError("Bundled references/standards.json is missing")
            actual = sha256(standards_path)
            expected = self.job.get("standards_sha256", "")
            standards = read_json(standards_path)
            actual_version = standards.get("version", standards.get("standards_version"))
            valid = bool(SHA_PATTERN.fullmatch(str(expected))) and actual == expected.lower()
            valid &= self.job.get("standards_version") == actual_version
            self.add("standards_identity", valid, {"expected_sha256": expected, "actual_sha256": actual,
                     "job_version": self.job.get("standards_version"), "actual_version": actual_version})
        except (OSError, ValueError, TypeError) as error:
            self.add("standards_identity", False, {"error": str(error)})

    def settings(self):
        scene, job = self.scene, self.job
        source, render = job.get("source", {}), job.get("render", {})
        dimensions = (scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage)
        native = (source.get("width"), source.get("height"), 100)
        requested = (render.get("width"), render.get("height"), render.get("percentage"))
        profile = job.get('profile', 'source-native')
        try:
            validate_render_contract(render, source, profile)
            resolution_valid = dimensions == requested
            resolution_error = None
        except (ValueError, KeyError, TypeError) as error:
            resolution_valid = False
            resolution_error = str(error)
        self.add("native_resolution_100_percent", resolution_valid,
                 {"saved": dimensions, "source": native, "requested": requested,
                  "profile": profile, "error": resolution_error})
        try:
            # Blender stores fps_base as float32 (1.001 is read back as
            # 1.0010000467...). Recover its ordinary rational representation
            # instead of flagging 30000/1001 as 30/1 or demanding impossible
            # bit-exact decimal storage. The source rational is unchanged.
            base = Fraction(str(scene.render.fps_base)).limit_denominator(10000)
            fps = Fraction(scene.render.fps, 1) / base
            expected = Fraction(int(source["fps_num"]), int(source["fps_den"]))
            self.add("source_frame_rate", fps == expected, {"saved": str(fps), "source": str(expected),
                     "saved_fps_base_raw": scene.render.fps_base, "saved_fps_base_rational": str(base)})
        except (KeyError, ValueError, ZeroDivisionError, TypeError) as error:
            self.add("source_frame_rate", False, {"error": str(error)})
        timeline = job.get("timeline", {})
        start, end = timeline.get("frame_start"), timeline.get("frame_end")
        frames_valid = isinstance(start, int) and not isinstance(start, bool) and isinstance(end, int) and not isinstance(end, bool) and 1 <= start <= end
        frames_valid &= (scene.frame_start, scene.frame_end) == (start, end)
        frames_valid &= isinstance(source.get("frame_count"), int) and frames_valid and end - start + 1 == source["frame_count"]
        self.add("source_frame_count", frames_valid, {"saved": [scene.frame_start, scene.frame_end], "requested": [start, end], "source_count": source.get("frame_count")})
        engine = scene.render.engine
        eevee = getattr(scene, "eevee", None)
        samples = getattr(eevee, "taa_render_samples", getattr(eevee, "taa_samples", None)) if eevee else None
        raytrace = getattr(eevee, "use_raytracing", None) if eevee else None
        required = render.get("samples")
        self.add("eevee_exact_64_samples", engine in {"BLENDER_EEVEE", "BLENDER_EEVEE_NEXT"} and samples == required == 64,
                 {"engine": engine, "saved_samples": samples, "requested_samples": required})
        dark = render.get("dark_scene")
        self.add("dark_scene_raytracing", raytracing_matches(render, raytrace, profile),
                 {"dark_scene": dark, "saved_raytracing": raytrace, "profile": profile,
                  "required_disabled": profile == 'client-4k-project-1080p', "unsupported_api_is_not_a_pass": True})
        rows = dependencies(scene)
        self.add("portable_external_dependencies", all(row["portable"] and row["exists"] for row in rows), {"dependencies": rows})
        self.final_bundle_dependencies(rows)
        return frames_valid

    def final_bundle_dependencies(self, rows):
        """The strict four-file bundle may depend only on its own original MP4.

        Candidate .blend files outside jobroot/delivery keep the general
        packed-or-existing-relative behavior. A final bundle also rejects
        absolute path strings, including stale absolute packed-file paths.
        """
        saved = Path(bpy.data.filepath).resolve() if bpy.data.filepath else None
        try:
            relative = saved.relative_to(self.root) if saved else None
        except ValueError:
            relative = None
        applicable = bool(relative and relative.parts and relative.parts[0] == "delivery")
        evidence = {"applicable": applicable, "passed": False, "dependencies": rows, "violations": []}
        if not applicable:
            evidence["scope"] = "Candidate scene outside the final delivery bundle; the general dependency gate applies"
            self.report["final_bundle_portability"] = evidence
            return
        structural = len(relative.parts) == 3 and relative.suffix.lower() == ".blend"
        name = relative.parts[1] if len(relative.parts) > 1 else ""
        structural &= bool(name) and saved.name == name + ".blend"
        declared_name = self.job.get("project_name")
        if declared_name is not None:
            structural &= declared_name == name
        original = saved.parent / (name + ".mp4")
        evidence.update({"bundle_name": name, "allowed_unpacked_resource": name + ".mp4",
                         "saved_blend_path": relative.as_posix(), "project_name": declared_name})
        if not structural:
            evidence["violations"].append({"kind": "bundle_structure", "error": "Expected delivery/<project_name>/<project_name>.blend"})
        for row in rows:
            errors = []
            if not row["relative"]:
                errors.append("Final bundle resource path must be Blender-relative, including packed resource path strings")
            if not row["exists"]:
                errors.append("Referenced resource is missing and not packed")
            if not row["packed"]:
                resolved = Path(bpy.path.abspath(row["path"])).resolve()
                if resolved != original.resolve():
                    errors.append("Unpacked resource is outside the sole allowed same-folder original MP4")
                if resolved.parent != saved.parent.resolve():
                    errors.append("Unpacked resource leaves the final four-file folder")
            if errors:
                evidence["violations"].append({"kind": row["kind"], "name": row["name"], "path": row["path"], "errors": errors})
        evidence["passed"] = not evidence["violations"]
        self.add("final_bundle_dependency_portability", evidence["passed"], evidence)
        self.report["final_bundle_portability"] = evidence

    def scene_objects(self):
        config = self.job.get("scene", {})
        names = config.get("production_objects", [])
        excluded = config.get("excluded_source_objects", [])
        known = {obj.name for obj in self.scene.objects}
        valid_lists = isinstance(names, list) and bool(names) and len(set(names)) == len(names)
        valid_lists &= isinstance(excluded, list) and not (set(names) & set(excluded))
        missing = sorted(set(names) - known) if isinstance(names, list) else ["production_objects must be a list"]
        self.add("production_inventory", valid_lists and not missing, {"missing": missing, "excluded_source_objects": excluded})
        excluded_missing = sorted(set(excluded) - known) if isinstance(excluded, list) else ["excluded_source_objects must be a list"]
        self.add("excluded_source_inventory", isinstance(excluded, list) and len(set(excluded)) == len(excluded) and not excluded_missing,
                 {"missing": excluded_missing, "objects": excluded})
        objects = [self.scene.objects[name] for name in names if name in known]
        renderable = renderable_members(self.scene)
        unlisted = [obj.name for obj in self.scene.objects if obj.type == "MESH" and not obj.hide_render and obj.name in renderable and obj.name not in set(names) | set(excluded)]
        self.add("unlisted_visible_meshes", not unlisted, {"objects": unlisted})
        bad_scale, bad_modifiers, image_nodes = [], [], []
        for obj in objects:
            if any(abs(float(v) - 1) > 1e-6 for v in obj.scale):
                bad_scale.append({"object": obj.name, "scale": list(obj.scale)})
            for modifier in obj.modifiers:
                if modifier.type != "ARMATURE":
                    bad_modifiers.append({"object": obj.name, "modifier": modifier.name, "type": modifier.type})
            if obj.type == "MESH":
                for material in obj.data.materials:
                    if material:
                        for node in image_texture_nodes(material.node_tree):
                            image_nodes.append({"object": obj.name, "material": material.name, "node": node})
        self.add("production_scale_one", not bad_scale, {"violations": bad_scale})
        self.add("static_modifiers_applied", not bad_modifiers, {"violations": bad_modifiers, "editable_armature_is_allowed": True})
        self.add("no_production_image_textures", not image_nodes, {"violations": image_nodes})
        linear = []
        for action in actions_for(objects):
            for curve in action_curves(action):
                for key in curve.keyframe_points:
                    if key.interpolation == "LINEAR":
                        linear.append({"action": action.name, "path": curve.data_path, "frame": float(key.co.x)})
        self.add("no_linear_animation", not linear, {"violations": linear[:30], "count": len(linear)})
        supports = config.get("support_objects", [])
        bad_support = [name for name in supports if name not in known or self.scene.objects[name].type != "MESH" or name not in names]
        self.add("support_inventory", isinstance(supports, list) and bool(supports) and not bad_support,
                 {"objects": supports, "invalid": bad_support})
        support_objects = [self.scene.objects[name] for name in supports if name in known and self.scene.objects[name].type == "MESH"]
        actors = config.get("actors", [])
        actor_errors, actor_data = [], []
        ids = [actor.get("id") for actor in actors]
        if not actors or len(set(ids)) != len(ids) or not all(isinstance(value, str) and value for value in ids):
            actor_errors.append({"error": "Nonempty unique actor IDs are required"})
        used_parts = set()
        for actor in actors:
            ident, parts = actor.get("id"), actor.get("parts", {})
            errors = []
            if set(parts) != set(PARTS) or len(set(parts.values())) != 10:
                errors.append("Exactly ten unique L3 part objects are required")
            for key, name in parts.items():
                if name not in known or self.scene.objects[name].type != "MESH" or name not in names or name in excluded:
                    errors.append("Missing/nonproduction mesh part: " + str(key))
                if name in used_parts:
                    errors.append("Part shared between actors: " + str(name))
            used_parts.update(parts.values())
            probes = actor.get("foot_probes", {})
            for side in ("L", "R"):
                probe = probes.get(side, {})
                part = "shin_" + side.lower()
                name, indices = probe.get("object"), probe.get("vertex_indices")
                if name != parts.get(part) or not isinstance(indices, list) or len(set(indices)) < 3:
                    errors.append("Explicit actual shin-cap polygon vertices are required for " + side)
                elif name in known:
                    obj = self.scene.objects[name]
                    valid_indices = all(isinstance(index, int) and not isinstance(index, bool) and 0 <= index < len(obj.data.vertices) for index in indices)
                    if not valid_indices or not any(set(poly.vertices) == set(indices) for poly in obj.data.polygons):
                        errors.append("Foot probe must be one complete actual mesh polygon: " + side)
            gait = actor.get("gait", [])
            gait_map = {row.get("frame"): row for row in gait if isinstance(row, dict)}
            if len(gait_map) != len(gait):
                errors.append("Duplicate or malformed gait frames")
            rig_name, bones = actor.get("armature"), actor.get("part_bones", {})
            rig = self.scene.objects.get(rig_name) if rig_name else None
            if rig_name:
                if not rig or rig.type != "ARMATURE" or rig.name not in names or set(bones) != set(PARTS):
                    errors.append("Armature and all ten part_bones mappings are required")
                else:
                    for part, bone_name in bones.items():
                        if bone_name not in rig.pose.bones:
                            errors.append("Missing mesh-bound bone: " + str(bone_name))
                            continue
                        obj = self.scene.objects.get(parts.get(part))
                        if not obj:
                            continue
                        mods = [mod for mod in obj.modifiers if mod.type == "ARMATURE" and mod.object == rig and mod.show_render]
                        group = obj.vertex_groups.get(bone_name)
                        bound = bool(mods) and group is not None
                        if group:
                            bound &= all(len(vertex.groups) == 1 and vertex.groups[0].group == group.index and abs(vertex.groups[0].weight - 1) < 1e-6 for vertex in obj.data.vertices)
                        if not bound:
                            errors.append("Part is not rigidly bound to its declared real bone: " + part)
                    # A caller cannot relabel a knee/top polygon as a sole to
                    # conceal floating or ground penetration. The actual
                    # distal shin face is checked in the real rest bone's
                    # knee-to-ankle (+Y) coordinate system.
                    for side in ("L", "R"):
                        part = "shin_" + side.lower()
                        obj = self.scene.objects.get(parts.get(part))
                        bone_name = bones.get(part)
                        probe = probes.get(side, {})
                        if not obj or bone_name not in rig.data.bones:
                            continue
                        bone = rig.data.bones[bone_name]
                        binding = rig.matrix_world.inverted() @ obj.matrix_world
                        coordinates = [bone.matrix_local.inverted() @ (binding @ vertex.co) for vertex in obj.data.vertices]
                        middle = (min(point.y for point in coordinates) + max(point.y for point in coordinates)) / 2
                        distal_faces = [poly for poly in obj.data.polygons if all(coordinates[index].y > middle for index in poly.vertices)]
                        distal = max(distal_faces, key=lambda poly: sum(coordinates[index].y for index in poly.vertices) / len(poly.vertices)) if distal_faces else None
                        if distal is None or set(probe.get("vertex_indices", [])) != set(distal.vertices):
                            errors.append("Probe is not the actual distal shin cap in rest-bone space: " + side)
            if errors:
                actor_errors.append({"actor": ident, "errors": errors})
            else:
                actor_data.append({"id": ident, "parts": {key: self.scene.objects[name] for key, name in parts.items()},
                                   "probes": probes, "gait": gait_map, "rig": rig, "bones": bones})
        self.add("actor_L3_and_foot_metadata", not actor_errors and bool(actor_data), {"violations": actor_errors})
        return objects, support_objects, actor_data

    def every_frame(self, objects, supports, actors):
        threshold = self.thresholds
        start, end = self.scene.frame_start, self.scene.frame_end
        initial_edges, previous_joints, previous_mesh_centers, pins = {}, {}, {}, {}
        flags = {key: [] for key in ("geometry", "visibility", "gait", "contact", "penetration", "slip", "rigidity", "joint_step", "joint_angle", "animated_scale", "source_exclusions", "inventory")}
        counters = Counter()
        extrema = {"minimum_cap_gap_m": None, "maximum_stance_gap_m": 0.0, "maximum_stance_drift_m": 0.0,
                   "maximum_edge_drift_m": 0.0, "maximum_joint_step_m": 0.0, "maximum_joint_angle_deg": 0.0}
        exceptions = self.job.get("scene", {}).get("approved_visibility_exceptions", [])
        excluded_names = set(self.job.get("scene", {}).get("excluded_source_objects", []))
        production_names = {obj.name for obj in objects}

        def flag(category, row):
            counters[category] += 1
            if len(flags[category]) < 30:
                flags[category].append(row)

        for frame in range(start, end + 1):
            self.scene.frame_set(frame)
            depsgraph = bpy.context.evaluated_depsgraph_get()
            renderable = renderable_members(self.scene)
            for obj in self.scene.objects:
                visible = not obj.hide_render and obj.name in renderable and getattr(obj, "visible_camera", True) is not False
                if visible and obj.name in excluded_names:
                    flag("source_exclusions", {"frame": frame, "object": obj.name, "error": "Excluded source object is actually render-visible"})
                if visible and obj.type == "MESH" and obj.name not in production_names | excluded_names:
                    flag("inventory", {"frame": frame, "object": obj.name, "error": "Render-visible mesh omitted from production inventory"})
            surfaces, highest = support_bvhs(supports, depsgraph)
            mesh_cache = {}
            for obj in objects:
                if any(abs(float(value) - 1) > 1e-6 for value in obj.scale):
                    flag("animated_scale", {"frame": frame, "object": obj.name, "scale": list(obj.scale)})
                if obj.type != "MESH":
                    continue
                points, edges, polygons = mesh_world(obj, depsgraph)
                mesh_cache[obj.name] = points
                if frame == start:
                    shape = mesh_shape(points, edges, polygons)
                    if not all(shape[key] for key in ("volumetric", "closed", "outward")):
                        flag("geometry", {"object": obj.name, **shape})
                lengths = [(points[a] - points[b]).length for a, b in edges]
                if obj.name not in initial_edges:
                    initial_edges[obj.name] = (edges, lengths)
                first_edges, first_lengths = initial_edges[obj.name]
                if edges != first_edges or len(lengths) != len(first_lengths):
                    flag("rigidity", {"frame": frame, "object": obj.name, "error": "Topology changed"})
                else:
                    drift = max((abs(a - b) for a, b in zip(lengths, first_lengths)), default=0.0)
                    extrema["maximum_edge_drift_m"] = max(extrema["maximum_edge_drift_m"], drift)
                    if drift > threshold["rigid_edge_drift_m"]:
                        flag("rigidity", {"frame": frame, "object": obj.name, "edge_drift_m": drift})
            for actor in actors:
                ident = actor["id"]
                for part, obj in actor["parts"].items():
                    invisible = obj.hide_render or obj.name not in renderable or getattr(obj, "visible_camera", True) is False
                    approved = any(row.get("actor") == ident and row.get("object") == obj.name and frame in row.get("frames", [])
                                   and row.get("approved") is True and isinstance(row.get("reason"), str) and row["reason"] for row in exceptions)
                    if invisible and not approved:
                        flag("visibility", {"frame": frame, "actor": ident, "part": part, "object": obj.name})
                    key = (ident, part)
                    points = mesh_cache[obj.name]
                    center = sum(points, Vector()) / len(points)
                    if key in previous_mesh_centers:
                        step = (center - previous_mesh_centers[key]).length
                        extrema["maximum_joint_step_m"] = max(extrema["maximum_joint_step_m"], step)
                        if step > threshold["max_joint_step_m"]:
                            flag("joint_step", {"frame": frame, "actor": ident, "part": part,
                                                "step_m": step, "measurement": "actual_evaluated_mesh_center"})
                    previous_mesh_centers[key] = center.copy()
                    # Only real mesh-bound bones are examined. Helpers, IK
                    # controls and hidden original source rigs are excluded.
                    if actor["rig"]:
                        rig = actor["rig"].evaluated_get(depsgraph)
                        bone = rig.pose.bones[actor["bones"][part]]
                        head, tail = rig.matrix_world @ bone.head, rig.matrix_world @ bone.tail
                        rotation = (rig.matrix_world @ bone.matrix).to_quaternion()
                    else:
                        evaluated = obj.evaluated_get(depsgraph)
                        points = mesh_cache[obj.name]
                        head = sum(points, Vector()) / len(points)
                        tail, rotation = head.copy(), evaluated.matrix_world.to_quaternion()
                    if key in previous_joints:
                        old_head, old_tail, old_rotation = previous_joints[key]
                        step = max((head - old_head).length, (tail - old_tail).length)
                        angle = math.degrees(rotation.rotation_difference(old_rotation).angle)
                        angle = min(angle, abs(360 - angle))
                        extrema["maximum_joint_step_m"] = max(extrema["maximum_joint_step_m"], step)
                        extrema["maximum_joint_angle_deg"] = max(extrema["maximum_joint_angle_deg"], angle)
                        if step > threshold["max_joint_step_m"]:
                            flag("joint_step", {"frame": frame, "actor": ident, "part": part, "step_m": step})
                        if angle > threshold["max_joint_angle_deg"]:
                            flag("joint_angle", {"frame": frame, "actor": ident, "part": part, "angle_deg": angle})
                    previous_joints[key] = (head.copy(), tail.copy(), rotation.copy())
                row = actor["gait"].get(frame)
                if not row or row.get("evidence") not in {"source_visible", "inferred", "source_verified_airborne"} or not isinstance(row.get("stance"), list) or len(set(row["stance"])) != len(row["stance"]) or any(side not in {"L", "R"} for side in row["stance"]):
                    flag("gait", {"frame": frame, "actor": ident, "error": "Valid per-frame gait evidence is required"})
                    row = {"stance": [], "evidence": "missing"}
                airborne = row["evidence"] == "source_verified_airborne"
                if airborne and (row["stance"] or not row.get("source_evidence")):
                    flag("gait", {"frame": frame, "actor": ident, "error": "Airborne exception needs no stance and a source_evidence reference"})
                    airborne = False
                if not airborne and not row["stance"]:
                    flag("gait", {"frame": frame, "actor": ident, "error": "NONE gait does not exempt ground contact"})
                actual_supported = []
                for side in ("L", "R"):
                    probe = actor["probes"][side]
                    points = mesh_cache.get(probe["object"], [])
                    try:
                        cap = [(index, points[index], contact_for(points[index], surfaces, highest)) for index in probe["vertex_indices"]]
                    except IndexError:
                        flag("contact", {"frame": frame, "actor": ident, "foot": side, "error": "Evaluated probe topology changed"})
                        continue
                    contacts = [(index, point, hit) for index, point, hit in cap if hit is not None]
                    if len(contacts) != len(cap):
                        flag("contact", {"frame": frame, "actor": ident, "foot": side, "error": "Cap lacks actual support surface under every vertex"})
                    for _, _, hit in contacts:
                        gap = hit["gap_m"]
                        old = extrema["minimum_cap_gap_m"]
                        extrema["minimum_cap_gap_m"] = gap if old is None else min(old, gap)
                        if gap < -threshold["penetration_m"]:
                            flag("penetration", {"frame": frame, "actor": ident, "foot": side, **hit})
                    grounded = any(-threshold["penetration_m"] <= hit["gap_m"] <= threshold["contact_gap_m"] for _, _, hit in contacts)
                    if grounded:
                        actual_supported.append(side)
                    key = (ident, side)
                    if side in row["stance"]:
                        if not grounded:
                            flag("contact", {"frame": frame, "actor": ident, "foot": side, "error": "Declared stance foot is not supported", "gaps_m": [hit["gap_m"] for _, _, hit in contacts]})
                        if contacts:
                            lowest = min(contacts, key=lambda item: abs(item[2]["gap_m"]))
                            extrema["maximum_stance_gap_m"] = max(extrema["maximum_stance_gap_m"], min(abs(item[2]["gap_m"]) for item in contacts))
                            if key not in pins:
                                pins[key] = (lowest[0], lowest[1].copy())
                            index, origin = pins[key]
                            current = points[index]
                            drift = math.hypot(current.x - origin.x, current.y - origin.y)
                            extrema["maximum_stance_drift_m"] = max(extrema["maximum_stance_drift_m"], drift)
                            if drift > threshold["stance_drift_m"]:
                                flag("slip", {"frame": frame, "actor": ident, "foot": side, "pinned_vertex": index, "cumulative_xy_drift_m": drift})
                    else:
                        pins.pop(key, None)
                if not airborne and not actual_supported:
                    flag("contact", {"frame": frame, "actor": ident, "error": "No actually supported foot"})
        names = {
            "geometry": "volumetric_closed_outward_meshes", "visibility": "actor_continuous_render_presence",
            "gait": "per_frame_gait_evidence", "contact": "actual_shin_cap_ground_support",
            "penetration": "actual_shin_cap_penetration", "slip": "stance_pinned_vertex_drift",
            "rigidity": "all_frame_rigid_mesh_edges", "joint_step": "real_bound_joint_position_continuity",
            "joint_angle": "real_bound_joint_rotation_continuity", "animated_scale": "all_frame_scale_one",
            "source_exclusions": "excluded_source_objects_hidden", "inventory": "all_frame_visible_production_inventory",
        }
        for key, name in names.items():
            self.add(name, counters[key] == 0, {"count": counters[key], "first_violations": flags[key], "frames_checked": end - start + 1})
        self.report["physical_extrema"] = extrema
        self.report["foot_probe_definition"] = "Explicit complete evaluated shin-cap polygon vertices; upward support surfaces from actual BVH; minimum contact gap; all cap vertices checked for penetration; one fixed mesh vertex pinned per continuous declared stance phase"

    def renders_check(self):
        if self.renders is None:
            self.report["render_evidence"] = {"provided": False, "passed": False, "scope": "Saved scene/frame evaluation only, not a rendered delivery"}
            return
        spec, source = self.job["render"], self.job["source"]
        timeline = self.job["timeline"]
        frames, errors = [], []
        for frame in range(timeline["frame_start"], timeline["frame_end"] + 1):
            path = self.renders / ("frame_%04d.png" % frame)
            try:
                row = png_information(path)
                if (row["width"], row["height"]) != delivery_dimensions(spec):
                    raise ValueError("Rendered dimensions differ from locked native delivery")
                frames.append({"frame": frame, **row})
            except (OSError, ValueError) as error:
                errors.append({"frame": frame, "error": str(error)})
        self.add("rendered_native_png_sequence", not errors, {"count": len(frames), "errors": errors[:30]})
        manifest_path = self.renders / "render_manifest.json"
        try:
            manifest = read_json(manifest_path)
            expected = {"schema": "client-white-model-render.v1", "status": "COMPLETE",
                        "executed_with": "Blender bpy actual renderer", "upscaled": False,
                        "job_id": self.job["job_id"], "blend_sha256": self.before_hash,
                        "standards_sha256": self.job["standards_sha256"], "width": delivery_dimensions(spec)[0],
                        "height": delivery_dimensions(spec)[1], "resolution_percentage": 100, "samples": spec["samples"],
                        "fps_num": source["fps_num"], "fps_den": source["fps_den"],
                        "frame_start": timeline["frame_start"], "frame_end": timeline["frame_end"],
                        "engine": self.scene.render.engine, "dark_scene": spec["dark_scene"]}
            if self.job.get('profile') == 'client-4k-project-1080p':
                expected['raytracing'] = False
                expected['saved_project_width'] = spec['width']
                expected['saved_project_height'] = spec['height']
            elif spec["dark_scene"]:
                expected["raytracing"] = True
            mismatches = {key: {"expected": value, "actual": manifest.get(key)} for key, value in expected.items() if manifest.get(key) != value}
            completed = manifest.get("completed", [])
            by_frame = {row.get("frame"): row for row in completed if isinstance(row, dict)}
            if len(by_frame) != len(completed) or len(completed) != timeline["frame_end"] - timeline["frame_start"] + 1:
                mismatches["completed_inventory"] = "Exact complete frame inventory is required"
            for frame in frames:
                evidence = by_frame.get(frame["frame"], {})
                if any(evidence.get(key) != frame[key] for key in ("frame", "path", "sha256", "width", "height")):
                    mismatches["completed_frame_" + str(frame["frame"])] = "PNG identity or dimensions differ from renderer manifest"
            self.add("render_scene_provenance", not mismatches, {"manifest_sha256": sha256(manifest_path), "mismatches": mismatches,
                     "scope": "Launcher provenance plus complete PNG checks; not visual approval"})
        except (OSError, ValueError) as error:
            self.add("render_scene_provenance", False, {"error": str(error), "required": "render_manifest.json generated by the actual render launcher"})
        render_checks = [row for row in self.report["checks"] if row["name"] in {"rendered_native_png_sequence", "render_scene_provenance"}]
        render_passed = len(render_checks) == 2 and all(row["passed"] for row in render_checks)
        self.report["render_evidence"] = {"provided": True, "passed": render_passed, "frames": frames, "count": len(frames), "errors": errors}

    def run(self):
        old_frame = self.scene.frame_current
        try:
            self.inputs()
            frames_valid = self.settings()
            objects, supports, actors = self.scene_objects()
            if frames_valid and supports and actors and all(isinstance(self.thresholds[key], (int, float)) and math.isfinite(self.thresholds[key]) for key in DEFAULT_THRESHOLDS):
                self.every_frame(objects, supports, actors)
            else:
                self.add("all_frame_evaluation", False, {"error": "Invalid timeline, supports, actors or thresholds; all-frame physics was not executed"})
            self.renders_check()
        except Exception as error:
            self.add("checker_execution", False, {"type": type(error).__name__, "error": str(error)})
        finally:
            self.scene.frame_set(old_frame)
            after = sha256(bpy.data.filepath) if self.before_hash else None
            self.add("readonly_blend_identity", bool(self.before_hash) and self.before_hash == after,
                     {"before_sha256": self.before_hash, "after_sha256": after})
            self.report["passed"] = bool(self.report["checks"]) and all(check["passed"] for check in self.report["checks"])
            atomic_json(self.report_path, self.report)
        return self.report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--renders")
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])
    try:
        result = Checker(args.job, args.report, args.renders).run()
    except Exception as error:
        result = {"schema": "client-white-model-blender-qa.v1", "passed": False, "visual_approval": False,
                  "engineering_thresholds_not_client": True,
                  "checks": [{"name": "job_load", "passed": False, "details": {"type": type(error).__name__, "error": str(error)}}]}
        atomic_json(args.report, result)
    print(json.dumps({"passed": result["passed"], "visual_approval": False, "report": str(Path(args.report).resolve())}))
    if not result["passed"]:
        raise RuntimeError("Blender QA failed; inspect the saved report")


if __name__ == "__main__":
    main()
