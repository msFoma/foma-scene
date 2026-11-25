"""Generate a spur gear train with measured pitch spacing and frame-driven rotation."""
import math
from dataclasses import dataclass

import bpy
from mathutils import Vector

PRESSURE_ANGLE = math.radians(20)
PHASE_SAMPLES = 32


def polar(radius, angle):
    return radius * math.cos(angle), radius * math.sin(angle)


def involute(angle):
    return math.tan(angle) - angle


@dataclass(frozen=True)
class Gear:
    teeth: int
    pitch: float
    root: float
    tip: float
    center: tuple[float, float]
    phase: float
    speed: float
    outline: list[tuple[float, float]]


def tooth_outline(teeth, module, backlash):
    pitch = teeth * module / 2
    root, tip = pitch - 1.25 * module, pitch + module
    base = pitch * math.cos(PRESSURE_ANGLE)
    start = max(root, base)
    step = math.tau / teeth
    half_at_pitch = step / 4 - backlash / (4 * pitch)

    def half_at(radius):
        return half_at_pitch + involute(PRESSURE_ANGLE) - involute(math.acos(min(1, base / radius)))

    radii = [start + (tip - start) * i / 6 for i in range(7)]
    half_start, half_tip = half_at(start), half_at(tip)
    shoulder = min(step * 0.035, (step / 2 - half_start) / 2)
    root_angle = half_start + shoulder
    profile = [(-root_angle, root)]
    profile.extend((-half_at(radius), radius) for radius in radii)
    profile.extend((-half_tip + 2 * half_tip * i / 4, tip) for i in range(1, 4))
    profile.extend((half_at(radius), radius) for radius in reversed(radii))
    profile.append((root_angle, root))
    profile.extend((root_angle + (step - 2 * root_angle) * i / 4, root) for i in range(1, 4))
    return pitch, root, tip, [(angle + index * step, radius) for index in range(teeth)
                             for angle, radius in profile]


def layout(teeth, module, backlash):
    placed = []
    for index, count in enumerate(teeth):
        pitch, root, tip, profile = tooth_outline(count, module, backlash)
        center, phase = (0.0, 0.0), 0.0
        if placed:
            previous = placed[-1]
            heading = math.radians(10 if index % 2 else -13)
            dx, dy = polar(pitch + previous.pitch, heading)
            center = previous.center[0] + dx, previous.center[1] + dy
            previous_at_contact = (heading - previous.phase) * previous.teeth / math.tau
            contact_phase = (0.5 - previous_at_contact) % 1
            phase = (heading + math.pi - contact_phase * math.tau / count) % (math.tau / count)
        placed.append(Gear(count, pitch, root, tip, center, phase, (-1) ** index * teeth[0] / count, profile))
    return placed


def edges_in_box(gear, input_angle, box):
    rotation = gear.phase + gear.speed * input_angle
    points = [(gear.center[0] + x, gear.center[1] + y)
              for angle, radius in gear.outline for x, y in (polar(radius, angle + rotation),)]
    return [(a, b) for a, b in zip(points, points[1:] + points[:1])
            if min(a[0], b[0]) <= box[2] and max(a[0], b[0]) >= box[0]
            and min(a[1], b[1]) <= box[3] and max(a[1], b[1]) >= box[1]]


def crosses(a, b, c, d):
    def orient(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    return (orient(a, b, c) * orient(a, b, d) < -1e-12
            and orient(c, d, a) * orient(c, d, b) < -1e-12)


def check_mesh(gears):
    contacts = []
    for index, (left, right) in enumerate(zip(gears, gears[1:]), start=1):
        distance = math.dist(left.center, right.center)
        low = [max(a - left.tip, b - right.tip) for a, b in zip(left.center, right.center)]
        high = [min(a + left.tip, b + right.tip) for a, b in zip(left.center, right.center)]
        for sample in range(PHASE_SAMPLES):
            angle = sample * math.tau / (gears[0].teeth * PHASE_SAMPLES)
            a = edges_in_box(left, angle, (*low, *high))
            b = edges_in_box(right, angle, (*low, *high))
            if any(crosses(p, q, r, s) for p, q in a for r, s in b):
                raise ValueError(f"Tooth outlines intersect in pair {index} at sample {sample}. Increase backlash.")
        contacts.append({"pair": [index, index + 1], "center_distance_mm": round(distance, 5),
                         "outline_phases_checked": PHASE_SAMPLES})
    return contacts


def ring_mesh(name, outline, inner_radii, thickness, units, spokes=0):
    """Close radial cells along the bore, tooth flanks and spoke openings."""
    count, rows = len(outline), len(inner_radii)
    vertices, faces = [], []
    for z in (-thickness / 2, thickness / 2):
        for ring in range(rows + 1):
            for angle, outer_radius in outline:
                r = outer_radius if ring == 0 else inner_radii[ring - 1]
                x, y = polar(r, angle)
                vertices.append((x * units, y * units, z * units))
    layer = count * (rows + 1)
    mask = {(row, index) for row in range(rows) for index in range(count)
            if not spokes or row != 1 or math.cos(spokes * outline[index][0]) > 0.57}
    for row, index in sorted(mask):
        j = (index + 1) % count
        loop = [row * count + index, row * count + j, (row + 1) * count + j, (row + 1) * count + index]
        faces.append([i + layer for i in loop])
        faces.append(tuple(reversed(loop)))
        neighbors = ((row - 1, index), (row, j), (row + 1, index), (row, (index - 1) % count))
        for side, neighbor in enumerate(neighbors):
            if neighbor not in mask:
                a, b = loop[side], loop[(side + 1) % 4]
                faces.append((a, b, b + layer, a + layer))
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    return mesh


class Assembly:
    def __init__(self, scene, label, units, origin):
        self.scene = scene
        self.units = units
        self.origin = Vector(origin) * units
        self.objects = []
        self.data = []
        self.old_camera = scene.camera
        self.collection = bpy.data.collections.new(label)
        scene.collection.children.link(self.collection)

    def create(self, name, block, collection, xyz, material=None):
        self.data.append((collection, block))
        obj = bpy.data.objects.new(name, block)
        self.objects.append(obj)
        self.collection.objects.link(obj)
        obj.location = self.origin + Vector(xyz) * self.units
        if material:
            block.materials.append(material)
        return obj

    def material(self, name, color, metallic, roughness):
        material = bpy.data.materials.new(name)
        self.data.append((bpy.data.materials, material))
        if bpy.app.version < (5, 0, 0):
            material.use_nodes = True
        shader = material.node_tree.nodes.get("Principled BSDF")
        shader.inputs["Base Color"].default_value = (*color, 1)
        shader.inputs["Metallic"].default_value = metallic
        shader.inputs["Roughness"].default_value = roughness
        material.diffuse_color = (*color, 1)
        return material

    def mesh_object(self, name, mesh, xyz, material, bevel=0):
        obj = self.create(name, mesh, bpy.data.meshes, xyz, material)
        if bevel:
            modifier = obj.modifiers.new("Machined edge", "BEVEL")
            modifier.width = bevel * self.units
            modifier.segments = 3
            modifier.harden_normals = True
            obj.modifiers.new("Weighted normals", "WEIGHTED_NORMAL").keep_sharp = True
        return obj

    def box(self, name, xyz, dim, material, bevel=0):
        x, y, z = [value * self.units / 2 for value in dim]
        verts = [(a * x, b * y, c * z) for c in (-1, 1) for a, b in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
        mesh = bpy.data.meshes.new(name)
        mesh.from_pydata(verts, [], [(3, 2, 1, 0), (4, 5, 6, 7), (0, 1, 5, 4),
                                     (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)])
        mesh.update()
        return self.mesh_object(name, mesh, xyz, material, bevel)

    def collar(self, name, xyz, outside, bore, height, material, bevel=0.25):
        outline = [(i * math.tau / 64, outside) for i in range(64)]
        return self.mesh_object(name, ring_mesh(name, outline, [bore], height, self.units), xyz, material, bevel)

    def aim(self, obj, xyz):
        obj.rotation_euler = (self.origin + Vector(xyz) * self.units - obj.location).to_track_quat("-Z", "Y").to_euler()

    def label(self, name, words, xyz, size, material):
        data = bpy.data.curves.new(name, "FONT")
        data.body = words
        data.size = size * self.units
        data.align_x = "CENTER"
        data.align_y = "CENTER"
        self.create(name, data, bpy.data.curves, xyz, material)

    def undo(self):
        if self.collection.children or set(self.collection.objects) - set(self.objects):
            raise ValueError("New scene items were added to the gear collection")
        if self.scene.camera in self.objects:
            self.scene.camera = self.old_camera
        for obj in reversed(self.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.collections.remove(self.collection)
        for collection, block in reversed(self.data):
            if not block.users:
                collection.remove(block)


def add_presentation(assembly, center, size, ink, module, ratio, teeth):
    x, y = center
    width, depth = size
    floor = assembly.material("Backdrop · blue graphite", (0.031, 0.044, 0.060), 0.05, 0.76)
    assembly.box("Studio backdrop", (x, y, -13), (width * 6, width * 6, 4), floor, 1)
    assembly.label("Ratio engraving", f"{ratio:.3g}:1  /  MODULE {module:g} MM", (x, y - depth / 2 + 13, 10.06), 6, ink)
    assembly.label("Tooth count engraving", "   /   ".join(f"{count}T" for count in teeth),
                   (x, y + depth / 2 - 10, 10.06), 5, ink)
    cameras = {}
    for name, xyz, target, scale in (
        ("GEAR TRAIN · Hero", (x + width * 0.41, y - width * 0.88, width * 0.75), (x, y, 15), width * 1.28),
        ("GEAR TRAIN · Plan", (x, y, width * 1.4), (x, y, 0), width * 1.16),
    ):
        data = bpy.data.cameras.new(name)
        data.type = "ORTHO"
        data.ortho_scale = scale * assembly.units
        data.clip_start = assembly.units
        data.clip_end = width * assembly.units * 12
        obj = assembly.create(name, data, bpy.data.cameras, xyz)
        assembly.aim(obj, target)
        cameras[name.rsplit(" ", 1)[-1].lower()] = obj.name
        if name.endswith("Hero") and assembly.scene.camera is None:
            assembly.scene.camera = obj
    for name, xyz, power, color, size_factor in (
        ("Key strip", (x - width * 0.65, y - width * 0.62, width * 0.90), 36, (0.79, 0.91, 1.0), 0.64),
        ("Warm rim", (x + width * 0.64, y + width * 0.56, width * 0.63), 41, (1.0, 0.73, 0.47), 0.48),
        ("Top fill", (x, y + width * 0.12, width * 1.15), 28, (0.80, 0.85, 1.0), 0.88),
    ):
        light = bpy.data.lights.new(f"GEAR TRAIN · {name}", "AREA")
        light.energy = power * (width * assembly.units / 0.34) ** 2
        light.color = color
        light.shape = "DISK"
        light.size = width * assembly.units * size_factor
        light_obj = assembly.create(light.name, light, bpy.data.lights, xyz)
        assembly.aim(light_obj, (x, y, 10))
    return cameras


def build(scene, *, teeth, module_mm, thickness_mm, backlash_mm, revolution_frames, origin_mm, studio):
    if not 2 <= len(teeth) <= 4 or any(not 18 <= count <= 96 for count in teeth):
        raise ValueError("Choose two to four gears with 18–96 teeth each")
    dimensions = [module_mm, thickness_mm, backlash_mm, *origin_mm]
    if not all(math.isfinite(value) for value in dimensions) or not 0 < backlash_mm <= module_mm / 2:
        raise ValueError("Use finite dimensions and backlash within half the module")
    units = 0.001 / scene.unit_settings.scale_length
    gears = layout(teeth, module_mm, backlash_mm)
    contacts = check_mesh(gears)
    minimum = [min(gear.center[i] - gear.tip for gear in gears) for i in range(2)]
    maximum = [max(gear.center[i] + gear.tip for gear in gears) for i in range(2)]
    center = [(a + b) / 2 for a, b in zip(minimum, maximum)]
    size = [b - a + 36 for a, b in zip(minimum, maximum)]
    ratio = teeth[-1] / teeth[0]
    assembly = Assembly(scene, "Spur gear train", units, origin_mm)
    try:
        graphite = assembly.material("Frame · graphite", (0.036, 0.051, 0.066), 0.55, 0.32)
        trim = assembly.material("Frame edge · titanium", (0.136, 0.178, 0.213), 0.77, 0.29)
        shafts = assembly.material("Axles · stainless steel", (0.36, 0.43, 0.48), 0.82, 0.29)
        engraving = assembly.material("Engraving · pearl", (0.57, 0.65, 0.70), 0.12, 0.50)
        assembly.box("Machined baseplate", (*center, 3), (*size, 14), graphite, 4)
        assembly.box("Inlaid base edge", (*center, 1.5), (size[0] + 0.7, size[1] + 0.7, 1.5), trim, 0.5)
        for side in (-1, 1):
            for end in (-1, 1):
                xy = (center[0] + side * (size[0] / 2 - 10), center[1] + end * (size[1] / 2 - 10))
                assembly.collar("Base fastener", (*xy, 10.6), 4.2, 1.5, 1.3, shafts, 0.20)
        z = 22 + thickness_mm / 2
        palette = ((0.019, 0.137, 0.306), (0.25, 0.33, 0.39), (0.48, 0.198, 0.040), (0.20, 0.38, 0.29))
        wheels = []
        for index, gear in enumerate(gears):
            role = "Input" if index == 0 else "Output" if index == len(gears) - 1 else "Idler"
            name = f"{index + 1:02d} {role} · {gear.teeth} teeth"
            color = assembly.material(f"{role} {index + 1} · machined metal", palette[index], 0.73, 0.27)
            axle = min(12, gear.root * 0.22)
            bore = axle + min(0.18, axle * 0.03)
            spokes = (6 if index == len(gears) - 1 else 5) if gear.teeth >= 26 else 0
            radii = [gear.root * 0.72, gear.root * 0.38, bore] if spokes else [bore]
            mesh = ring_mesh(name, gear.outline, radii, thickness_mm, units, spokes)
            wheel = assembly.mesh_object(name, mesh, (*gear.center, z), color, min(0.30, module_mm * 0.08))
            wheel["teeth"] = gear.teeth
            wheel["module_mm"] = module_mm
            wheel["pitch_diameter_mm"] = gear.pitch * 2
            wheel["speed_relative_to_input"] = gear.speed
            wheel.rotation_mode = "XYZ"
            driver = wheel.driver_add("rotation_euler", 2).driver
            driver.expression = f"{gear.phase!r} + ({gear.speed * math.tau / revolution_frames!r}) * (frame - 1)"
            assembly.collar(f"{role} · bearing seat", (*gear.center, 15), axle * 1.57, axle, 10, trim)
            assembly.collar(f"{role} · fixed shaft", (*gear.center, (z + thickness_mm / 2 + 17) / 2),
                            axle, axle * 0.14, z + thickness_mm / 2 + 3, shafts)
            assembly.collar(f"{role} · retaining washer", (*gear.center, z + thickness_mm / 2 + 1.9),
                            axle * 1.31, axle * 0.32, 2.1, shafts)
            wheels.append(wheel)
        cameras = add_presentation(assembly, center, size, engraving, module_mm, ratio, teeth) if studio else {}
        bpy.context.view_layer.update()
        return assembly, wheels, {"module_mm": module_mm, "pressure_angle_deg": 20, "backlash_mm": backlash_mm,
                                  "reduction_ratio": ratio, "input_revolution_frames": revolution_frames,
                                  "output_direction": "same" if gears[-1].speed > 0 else "opposite",
                                  "meshing": contacts, "cameras": cameras}
    except Exception:
        assembly.undo()
        raise
