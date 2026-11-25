"""Build the two curved lids and keep a real background Blender session open."""
import math
import os
import sys
import time
from pathlib import Path

import bpy
from mathutils import Vector

root = Path(__file__).resolve().parents[1]


def point_at(obj, point):
    obj.rotation_euler = (Vector(point) - obj.location).to_track_quat("-Z", "Y").to_euler()


def camera(name, position, target, scale):
    data = bpy.data.cameras.new(name)
    obj = bpy.data.objects.new(name, data)
    bpy.context.collection.objects.link(obj)
    obj.location = position
    point_at(obj, target)
    data.type = "ORTHO"
    data.ortho_scale = scale
    return obj


def lid(name, x, material):
    verts, faces = [], []
    rings = [(0, 0.0084), (0.25, 0.0083), (0.56, 0.0079),
             (0.80, 0.0072), (0.96, 0.00625), (1, 0.0048),
             (1, 0.0012), (0.98, 0), (0, 0)]
    count = 64
    loops = []
    for radius, z in rings:
        ring = []
        count_ring = count if radius else 1
        for i in range(count_ring):
            angle = 2 * math.pi * i / count_ring
            ring.append(len(verts))
            verts.append((radius * 0.062 * math.cos(angle), radius * 0.048 * math.sin(angle), z))
        loops.append(ring)
    for a, b in zip(loops, loops[1:]):
        for i in range(count):
            nxt = (i + 1) % count
            if len(a) == 1:
                faces.append((a[0], b[i], b[nxt]))
            elif len(b) == 1:
                faces.append((a[i], b[0], a[nxt]))
            else:
                faces.append((a[i], b[i], b[nxt], a[nxt]))
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    obj.location.x = x
    obj.data.materials.append(material)
    for poly in mesh.polygons:
        poly.use_smooth = True
    return obj


def build(path):
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = 1
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 10
    scene.cycles.use_denoising = False
    scene.cycles.seed = 19
    scene.render.threads_mode = "FIXED"
    scene.render.threads = 4
    scene.render.resolution_x, scene.render.resolution_y = 700, 420
    scene.render.resolution_percentage = 100
    scene.render.image_settings.compression = 85
    scene.world.use_nodes = True
    scene.world.node_tree.nodes["Background"].inputs[0].default_value = (0.27, 0.30, 0.38, 1)
    scene.world.node_tree.nodes["Background"].inputs[1].default_value = 0.7
    scene.view_settings.view_transform = "AgX"
    material = bpy.data.materials.new("Shared · graphite polymer")
    material.use_nodes = True
    shader = material.node_tree.nodes.get("Principled BSDF")
    shader.inputs["Base Color"].default_value = (0.034, 0.087, 0.12, 1)
    shader.inputs["Roughness"].default_value = 0.28
    shader.inputs["Metallic"].default_value = 0.2
    target = lid("Lid A (selected)", -0.076, material)
    neighbor = lid("Lid B (shared material)", 0.076, material)
    lids = bpy.data.collections.new("Lids")
    scene.collection.children.link(lids)
    for obj in (target, neighbor):
        for collection in list(obj.users_collection):
            collection.objects.unlink(obj)
        lids.objects.link(obj)
    scene.camera = camera("Top", (0, 0, 0.53), (0, 0, 0), 0.31)
    camera("Underside", (0, 0, -0.53), (0, 0, 0), 0.31)
    camera("Close-up", (-0.109, -0.092, 0.14), (-0.076, 0, 0.004), 0.145)
    light_data = bpy.data.lights.new("Softbox", "AREA")
    light_data.energy = 7
    light_data.shape = "DISK"
    light_data.size = 0.30
    light = bpy.data.objects.new("Softbox", light_data)
    bpy.context.collection.objects.link(light)
    light.location = (-0.06, -0.08, 0.25)
    point_at(light, (0, 0, 0))
    target.select_set(True)
    bpy.context.view_layer.objects.active = target
    bpy.context.view_layer.update()
    for area in (bpy.context.screen.areas if bpy.context.screen else ()):
        if area.type == "VIEW_3D":
            area.spaces.active.region_3d.view_distance = 0.45
    if path:
        bpy.ops.wm.save_as_mainfile(filepath=str(path))


def select(*names):
    bpy.ops.object.select_all(action="DESELECT")
    for name in names:
        obj = bpy.data.objects[name]
        obj.select_set(True)
    bpy.context.view_layer.objects.active = bpy.data.objects[names[0]]


def cube(name, location, scale, material=None):
    bpy.ops.mesh.primitive_cube_add(size=1, location=location)
    obj = bpy.context.object
    obj.name, obj.scale = name, scale
    if material:
        obj.data.materials.append(material)
    return obj


def build_acceptance(out):
    material = bpy.data.materials.new("Case surface")
    material.use_nodes = True
    material.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.07, 0.13, 0.19, 1)
    target = (0.5, 0, 0)
    body = cube("Case", target, (0.10, 0.06, 0.054), material)
    cube("Case cutter", (0.528, 0, 0.01), (0.022, 0.095, 0.018))
    body.modifiers.new("Keep after cut", "TRIANGULATE")
    cube("Case screw", (0.47, -0.026, 0.012), (0.006, 0.006, 0.006), material)
    cube("Case port", (0.52, -0.026, 0), (0.013, 0.006, 0.009), material)
    camera("Case front", (0.5, -0.40, 0), target, 0.20)
    camera("Case side", (0.90, 0, 0), target, 0.20)
    camera("Case back", (0.5, 0.40, 0), target, 0.20)
    export_group = bpy.data.collections.new("GLB fixture")
    bpy.context.scene.collection.children.link(export_group)
    plain = bpy.data.materials.new("Export PBR")
    plain.use_nodes = True
    node = plain.node_tree.nodes.new("ShaderNodeTexImage")
    node.image = bpy.data.images.load(str(out / "logo.png"), check_existing=True)
    plain.node_tree.links.new(node.outputs["Color"], plain.node_tree.nodes["Principled BSDF"].inputs["Base Color"])
    textured = cube("GLB textured part", (-0.32, 0.01, 0.03), (0.05, 0.04, 0.028), plain)
    parent = bpy.data.objects.new("GLB root", None)
    parent.location = (-0.04, 0.03, 0.02)
    export_group.objects.link(parent)
    for collection in list(textured.users_collection):
        collection.objects.unlink(textured)
    export_group.objects.link(textured)
    textured.parent = parent
    textured.rotation_euler.z = 0.51
    bpy.context.view_layer.update()


def build_large():
    scene = bpy.context.scene
    group = bpy.data.collections.new("Scale fixture")
    dense_group = bpy.data.collections.new("Dense instances")
    scene.collection.children.link(group)
    group.children.link(dense_group)
    group.hide_render = True
    subdivisions = 160
    width = subdivisions + 1
    vertices = [(x * 0.001, y * 0.001, 0) for y in range(width) for x in range(width)]
    quads = [(a, a + 1, a + width + 1, a + width)
             for y in range(subdivisions) for x in range(subdivisions)
             for a in (y * width + x,)]
    mesh = bpy.data.meshes.new("Dense shared mesh")
    mesh.from_pydata(vertices, (), quads)
    mesh.update()
    source = bpy.data.objects["Lid A (selected)"]
    mesh.materials.append(source.active_material)
    for index in range(5000):
        dense = index < 200
        obj = bpy.data.objects.new(f"Scale part {index:04}", mesh if dense else source.data)
        obj.location = ((index % 100) * 0.3, (index // 100) * 0.3, 0)
        (dense_group if dense else group).objects.link(obj)
    bpy.context.view_layer.update()
    select("Scale part 0000")


def pump_manual(out):
    path = out / "manual.pending"
    if not path.exists():
        return 0.03
    command = path.read_text()
    path.unlink()
    if command == "neighbor":
        select("Lid B (shared material)")
    elif command == "lid":
        select("Lid A (selected)")
    elif command in {"shift-top-camera", "restore-top-camera"}:
        bpy.data.objects["Top"].location.x = 0.02 if command == "shift-top-camera" else 0
    elif command == "build":
        build_acceptance(out)
    elif command == "build-large":
        build_large()
    elif command == "change-shared-mesh":
        mesh = bpy.data.meshes["Dense shared mesh"]
        mesh.vertices[0].co.z = 0.004
        mesh.update()
    elif command == "checkpoint-relative-texture":
        image = bpy.data.images.load(str(out / "logo.png"), check_existing=False)
        image.name = "Checkpoint external texture"
        image.filepath = "//logo.png"
        image.use_fake_user = True
    elif command == "check-original-texture-path":
        assert bpy.data.images["Checkpoint external texture"].filepath == "//logo.png"
    elif command in {"change-layer-texture", "restore-layer-texture"}:
        material = bpy.context.active_object.active_material
        layer = next(node for node in material.node_tree.nodes if "blender_mcp_layer" in node)
        image = next(node for node in layer.node_tree.nodes if node.type == "TEX_IMAGE")
        image.extension = "REPEAT" if command == "change-layer-texture" else "CLIP"
    elif command == "only-body":
        select("Case")
    elif command == "body-and-cutter":
        select("Case", "Case cutter")
    elif command == "viewport-solid":
        area = next(area for area in bpy.context.window.screen.areas if area.type == "VIEW_3D")
        space = area.spaces.active
        space.overlay.show_overlays = False
        space.region_3d.view_rotation = bpy.data.objects["Top"].rotation_euler.to_quaternion()
        space.region_3d.view_perspective = "ORTHO"
        space.region_3d.view_location = (0, 0, 0)
        space.region_3d.view_distance = 0.24
        space.shading.type = "SOLID"
        area.tag_redraw()
    elif command == "shader-editor":
        area = next(area for area in bpy.context.window.screen.areas if area.type == "PROPERTIES")
        area.type = "NODE_EDITOR"
        area.ui_type = "ShaderNodeTree"
    elif command in {"missing-screw", "shifted-port", "wrong-depth", "correct"}:
        bpy.data.objects["Case screw"].hide_render = command == "missing-screw"
        bpy.data.objects["Case port"].location.x = 0.54 if command == "shifted-port" else 0.52
        bpy.data.objects["Case"].scale.y = 0.09 if command == "wrong-depth" else 0.06
    else:
        raise ValueError(command)
    bpy.context.view_layer.update()
    (out / "manual.done").write_text(command)
    return 0.03


def main():
    out = Path(os.environ["BLENDER_MCP_TEST_OUTPUT"])
    out.mkdir(parents=True, exist_ok=True)
    build(out / "two-lids.blend")
    if bpy.app.background:
        sys.path.insert(0, str(root))
        import blender_bridge

        blender_bridge.register()
        bridge = blender_bridge.start(use_timer=False)
    else:
        for op, args in ((bpy.ops.preferences.addon_install, {"filepath": os.environ["BLENDER_MCP_TEST_ADDON_ZIP"]}),
                         (bpy.ops.preferences.addon_enable, {"module": "blender_bridge"})):
            if "FINISHED" not in op(**args):
                raise RuntimeError(f"Blender could not run {op.idname()}")

        preferences = bpy.context.preferences.addons["blender_bridge"].preferences
        preferences.token = os.environ.pop("BLENDER_MCP_TOKEN")
        preferences.port = int(os.environ.pop("BLENDER_MCP_BRIDGE_PORT"))
        if "FINISHED" not in bpy.ops.wm.blender_mcp_toggle():
            raise RuntimeError("Blender could not connect through Add-on Preferences")

        import blender_bridge

        bridge = blender_bridge.bridge
    (out / "manual.pending").unlink(missing_ok=True)
    if not bpy.app.background:
        bpy.app.timers.register(lambda: pump_manual(out))
    (out / "blender.ready").write_text(str(bridge.server.server_port))
    if bpy.app.background:
        try:
            while not (out / "blender.stop").exists():
                pump_manual(out)
                time.sleep(bridge.pump())
        finally:
            blender_bridge.unregister()


if __name__ == "__main__":
    main()
