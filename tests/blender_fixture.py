"""Build tiny isolated QA fixtures. Never load or alter a client project.

This test geometry is not a client reference/video reconstruction and does not
represent visual/client approval. All ten parts are genuinely mesh-bound to
ten real bones. The negative variants deliberately violate machine gates.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import zlib

import bpy
from mathutils import Vector


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tiny_png(path):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    content = b"\x89PNG\r\n\x1a\n"
    content += chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    content += chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff"))
    content += chunk(b"IEND", b"")
    Path(path).write_bytes(content)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--standards", required=True)
    parser.add_argument("--variant", default="positive")
    parser.add_argument("--render", action="store_true")
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    root = Path(args.output).resolve()
    root.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    # The enum is dynamic in some versions; the guarded assignment below is
    # deliberately independent of installed add-on render engines.
    try:
        scene.render.engine = "BLENDER_EEVEE_NEXT"
    except TypeError:
        scene.render.engine = "BLENDER_EEVEE"
    eevee = scene.eevee
    if hasattr(eevee, "taa_render_samples"):
        eevee.taa_render_samples = 64
    else:
        eevee.taa_samples = 64
    if hasattr(eevee, "use_raytracing"):
        eevee.use_raytracing = True
    scene.render.resolution_x = 3840
    scene.render.resolution_y = 2160
    scene.render.resolution_percentage = 100
    scene.render.fps = 24
    scene.render.fps_base = 1
    if args.variant == "fractional_fps":
        scene.render.fps = 30
        scene.render.fps_base = 1.001
    scene.frame_start = 1
    scene.frame_end = 1 if args.render else 3
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    if args.variant == "wrong_resolution":
        scene.render.resolution_percentage = 25
    if args.variant in {"low_samples", "high_samples"}:
        sample_count = 16 if args.variant == "low_samples" else 128
        if hasattr(eevee, "taa_render_samples"):
            eevee.taa_render_samples = sample_count
        else:
            eevee.taa_samples = sample_count

    parts = {}
    poses = {
        "torso": ((0, 0, 1.5), (.6, .35, .7)),
        "upper_arm_l": ((-.45, 0, 1.55), (.22, .22, .5)),
        "forearm_l": ((-.45, 0, 1.0), (.2, .2, .5)),
        "upper_arm_r": ((.45, 0, 1.55), (.22, .22, .5)),
        "forearm_r": ((.45, 0, 1.0), (.2, .2, .5)),
        "thigh_l": ((-.2, 0, .875), (.24, .25, .55)),
        "shin_l": ((-.2, 0, .25), (.22, .25, .5)),
        "thigh_r": ((.2, 0, .875), (.24, .25, .55)),
        "shin_r": ((.2, 0, .25), (.22, .25, .5)),
    }
    for part, (location, dimensions) in poses.items():
        bpy.ops.mesh.primitive_cube_add(size=1, location=location)
        obj = bpy.context.object
        obj.name = "ACTOR_A_" + part
        obj.dimensions = dimensions
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
        parts[part] = obj.name
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2, radius=.25, location=(0, 0, 2.1))
    bpy.context.object.name = "ACTOR_A_head"
    parts["head"] = bpy.context.object.name
    bpy.ops.object.armature_add(location=(0, 0, 0))
    rig = bpy.context.object
    rig.name = "ACTOR_A_rig"
    bpy.ops.object.mode_set(mode="EDIT")
    for bone in list(rig.data.edit_bones):
        rig.data.edit_bones.remove(bone)
    for part, name in parts.items():
        bone = rig.data.edit_bones.new(part)
        center = bpy.data.objects[name].location
        if part.startswith("shin_"):
            bone.head = center + Vector((0, 0, .25))
            bone.tail = center - Vector((0, 0, .25))
        else:
            bone.head = center
            bone.tail = bone.head + Vector((0, 0, .1))
    bpy.ops.object.mode_set(mode="OBJECT")
    for part, name in parts.items():
        obj = bpy.data.objects[name]
        group = obj.vertex_groups.new(name=part)
        group.add(list(range(len(obj.data.vertices))), 1.0, "REPLACE")
        modifier = obj.modifiers.new("Editable rigid binding", "ARMATURE")
        modifier.object = rig
        obj.parent = rig
    if args.variant == "floating":
        rig.location.z = .2
    if args.variant == "penetration":
        rig.location.z = -.02
    if args.variant in {"slide", "joint_jump", "linear"}:
        rig.location.x = 0
        rig.keyframe_insert("location", frame=1)
        rig.location.x = .06 if args.variant != "joint_jump" else .5
        rig.keyframe_insert("location", frame=2)
        rig.keyframe_insert("location", frame=3)
        if args.variant == "linear":
            for layer in rig.animation_data.action.layers:
                for strip in layer.strips:
                    for bag in strip.channelbags:
                        for curve in bag.fcurves:
                            for key in curve.keyframe_points:
                                key.interpolation = "LINEAR"
    if args.variant == "scaled":
        bpy.data.objects[parts["head"]].scale.x = 1.1
    if args.variant == "hidden":
        bpy.data.objects[parts["forearm_l"]].hide_render = True
    if args.variant == "hidden_parent_collection":
        parent = bpy.data.collections.new("Hidden parent")
        child = bpy.data.collections.new("Visible child")
        scene.collection.children.link(parent)
        parent.children.link(child)
        obj = bpy.data.objects[parts["forearm_l"]]
        for collection in list(obj.users_collection):
            collection.objects.unlink(obj)
        child.objects.link(obj)
        parent.hide_render = True
    if args.variant == "object_jump_with_static_bone":
        obj = bpy.data.objects[parts["forearm_l"]]
        obj.keyframe_insert("location", frame=1)
        obj.location.x += .5
        obj.keyframe_insert("location", frame=2)
        obj.keyframe_insert("location", frame=3)
    if args.variant == "image_texture":
        material = bpy.data.materials.new("Forbidden image material")
        material.use_nodes = True
        material.node_tree.nodes.new("ShaderNodeTexImage")
        bpy.data.objects[parts["torso"]].data.materials.append(material)
    if args.variant == "static_modifier":
        bpy.data.objects[parts["head"]].modifiers.new("Unapplied bevel", "BEVEL")
    if args.variant == "missing_external_image":
        image = bpy.data.images.new("Missing external reference", width=1, height=1)
        image.source = "FILE"
        image.filepath = "//missing-reference.png"
        image.use_fake_user = True

    bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, -.025))
    support = bpy.context.object
    support.name = "ENV_Ground"
    support.dimensions = (8, 8, .05)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    bpy.ops.object.camera_add(location=(4, -6, 3))
    camera = bpy.context.object
    camera.name = "CAMERA"
    camera.rotation_euler = (Vector((0, 0, 1.2)) - camera.location).to_track_quat("-Z", "Y").to_euler()
    scene.camera = camera
    bpy.ops.object.light_add(type="SUN", location=(1, -3, 5))
    light = bpy.context.object
    light.name = "LIGHT"
    light.data.energy = 2
    light.rotation_euler = (.2, -.3, -.4)
    excluded_source = []
    if args.variant in {"visible_excluded_source", "hidden_excluded_source", "source_becomes_visible"}:
        bpy.ops.mesh.primitive_cube_add(size=.4, location=(1, 1, 1))
        original = bpy.context.object
        original.name = "SOURCE_TEMPLATE_OriginalPart"
        excluded_source.append(original.name)
        original.hide_render = args.variant != "visible_excluded_source"
        if args.variant == "source_becomes_visible":
            original.keyframe_insert("hide_render", frame=1)
            original.hide_render = False
            original.keyframe_insert("hide_render", frame=2)
            original.keyframe_insert("hide_render", frame=3)
    scene.world = bpy.data.worlds.new("World")
    scene.world.color = (.15, .15, .15)
    scene.frame_set(1)
    final = args.variant.startswith("final_")
    name = "fixture"
    blend = root / "delivery" / name / (name + ".blend") if final else root / "candidate.blend"
    blend.parent.mkdir(parents=True, exist_ok=True)
    if final:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("FFmpeg is required for actual final-bundle movieclip fixtures")
        original = blend.parent / (name + ".mp4")
        subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=white:s=16x16:r=24", "-frames:v", "3", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-y", str(original)], check=True, capture_output=True)
        reference = original
        relative_reference = "//" + original.name
        if args.variant == "final_parent_dependency":
            reference = root / "inputs" / "reference.mp4"
            reference.parent.mkdir()
            reference.write_bytes(original.read_bytes())
            relative_reference = "//../../inputs/reference.mp4"
        clip = bpy.data.movieclips.load(str(reference))
        clip.name = "Original comparison reference"
        clip.use_fake_user = True
        clip.filepath = str(original) if args.variant == "final_absolute_dependency" else relative_reference
        if args.variant in {"final_non_original_asset", "final_packed_asset", "final_absolute_packed_asset"}:
            png = root / "fixture-reference.png"
            tiny_png(png)
            image = bpy.data.images.load(str(png))
            image.use_fake_user = True
            if args.variant == "final_non_original_asset":
                target = blend.parent / png.name
                target.write_bytes(png.read_bytes())
                image.filepath = "//" + target.name
            else:
                image.pack()
                image.filepath = str(png) if args.variant == "final_absolute_packed_asset" else "//packed-reference.png"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend), relative_remap=False)
    # This synthetic source byte fixture tests identity verification only;
    # real media decode/spec checks live in the separate delivery gate.
    (root / "source.fixture").write_bytes(b"Synthetic source identity fixture\n")
    (root / "template.blend").write_bytes(blend.read_bytes())
    foot_probes = {}
    for side in ("L", "R"):
        obj = bpy.data.objects[parts["shin_" + side.lower()]]
        minimum = min(vertex.co.z for vertex in obj.data.vertices)
        indices = [vertex.index for vertex in obj.data.vertices if abs(vertex.co.z - minimum) < 1e-6]
        foot_probes[side] = {"object": obj.name, "vertex_indices": indices}
    if args.variant == "wrong_cap":
        obj = bpy.data.objects[parts["shin_l"]]
        maximum = max(vertex.co.z for vertex in obj.data.vertices)
        foot_probes["L"]["vertex_indices"] = [vertex.index for vertex in obj.data.vertices if abs(vertex.co.z - maximum) < 1e-6]
    gait = [{"frame": frame, "stance": ["L", "R"], "evidence": "inferred"} for frame in range(1, scene.frame_end + 1)]
    if args.variant == "none_gait":
        gait = [{"frame": frame, "stance": [], "evidence": "inferred"} for frame in range(1, scene.frame_end + 1)]
    if args.variant == "missing_probe":
        foot_probes.pop("R")
    job = {
        "schema": "client-white-model-job.v1", "job_id": "synthetic-" + args.variant,
        "standards_version": "1.0.0", "standards_sha256": sha(args.standards),
        "source": {"path": "source.fixture", "sha256": sha(root / "source.fixture"), "width": 3840, "height": 2160, "fps_num": 24, "fps_den": 1, "frame_count": scene.frame_end},
        "template": {"path": "template.blend", "sha256": sha(root / "template.blend"), "level": "L3"},
        "render": {"width": 3840, "height": 2160, "percentage": 100, "samples": 64, "dark_scene": True},
        "timeline": {"frame_start": 1, "frame_end": scene.frame_end},
        "scene": {"actors": [{"id": "A", "parts": parts, "armature": rig.name,
                               "part_bones": {part: part for part in parts}, "foot_probes": foot_probes, "gait": gait}],
                  "production_objects": [*parts.values(), rig.name, support.name, camera.name, light.name],
                  "support_objects": [support.name], "excluded_source_objects": excluded_source, "approved_visibility_exceptions": []},
    }
    if args.variant == "wrong_source_hash":
        job["source"]["sha256"] = "0" * 64
    if args.variant == "wrong_standards_hash":
        job["standards_sha256"] = "0" * 64
    if args.variant == "missing_support":
        job["scene"]["support_objects"] = []
    if args.variant == "missing_gait_frame":
        job["scene"]["actors"][0]["gait"] = gait[:-1]
    if args.variant == "fractional_fps":
        job["source"]["fps_num"] = 30000
        job["source"]["fps_den"] = 1001
    if args.variant == "wrong_frame_count":
        job["source"]["frame_count"] = 2
    if final:
        job["project_name"] = name
    (root / "job.json").write_text(json.dumps(job, indent=2) + "\n", encoding="utf-8")
    if args.render:
        renders = root / "renders"
        renders.mkdir()
        path = renders / "frame_0001.png"
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
        manifest = {
            "schema": "client-white-model-render.v1", "status": "COMPLETE",
            "executed_with": "Blender bpy actual renderer", "upscaled": False,
            "job_id": job["job_id"], "blend_sha256": sha(blend), "standards_sha256": job["standards_sha256"],
            "width": 3840, "height": 2160, "resolution_percentage": 100, "samples": 64,
            "fps_num": 24, "fps_den": 1, "frame_start": 1, "frame_end": 1,
            "engine": scene.render.engine, "dark_scene": True, "raytracing": True,
            "completed": [{"frame": 1, "path": path.name, "sha256": sha(path), "width": 3840, "height": 2160}],
        }
        (renders / "render_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
