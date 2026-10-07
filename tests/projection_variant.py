"""Alter only a loaded isolated synthetic fixture; save a separate test file."""
import argparse
from pathlib import Path
import sys

import bpy


p = argparse.ArgumentParser()
p.add_argument('--variant', required=True, choices=['centimetres', 'camera-marker'])
p.add_argument('--output', required=True)
args = p.parse_args(sys.argv[sys.argv.index('--')+1:])
output = Path(args.output).resolve()
loaded = Path(bpy.data.filepath).resolve()
if output == loaded or output.exists() or 'previs-render-contract-' not in str(output):
    raise ValueError('Only a new separate isolated test path is supported')
scene = bpy.context.scene
if args.variant == 'centimetres':
    scene.unit_settings.scale_length = .01
else:
    old = scene.camera
    initial = scene.timeline_markers.new('SYNTHETIC_INITIAL_CAMERA', frame=1)
    initial.camera = old
    bpy.ops.object.camera_add(location=(6, -3, 4))
    other = bpy.context.object
    marker = scene.timeline_markers.new('SYNTHETIC_CAMERA_CHANGE', frame=2)
    marker.camera = other
    scene.camera = old
    scene.frame_set(1)
bpy.ops.wm.save_as_mainfile(filepath=str(output), relative_remap=False)
