"""Orthographic overlays of evaluated geometry against annotated alpha masks."""
import base64
import hashlib
import math
from pathlib import Path

import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector

from .observation import png_rgba


def file_digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate(spec):
    if len(set(spec["subjects"])) != len(spec["subjects"]):
        raise ValueError("Reference subjects must be unique object names")
    views = spec["views"]
    if len({view["camera"] for view in views}) != len(views):
        raise ValueError("Reference camera names must be unique")
    for view in views:
        camera(bpy.context.scene, view["camera"], view["role"])
        path = Path(view["mask_path"]).expanduser().resolve(strict=True)
        if path.suffix.lower() != ".png":
            raise ValueError("Reference masks must be PNG")
        view["mask_path"], view["sha256"] = str(path), file_digest(path)
        read_mask(path)
    return spec


def camera(scene, name, role):
    cam = scene.objects.get(name)
    if cam is None or cam.type != "CAMERA" or cam.data.type != "ORTHO":
        raise ValueError(f"{name}: reference comparison requires a named orthographic camera")
    if role == "side":
        direction = cam.matrix_world.to_quaternion() @ Vector((0, 0, -1))
        if abs(direction.x) < 0.95:
            raise ValueError(f"{name}: side camera must look approximately along world X to measure world Y depth")
    return cam


def read_mask(path):
    from array import array

    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        width, height = image.size
        if not (16 <= width <= 1024 and 16 <= height <= 1024) or image.channels != 4:
            raise ValueError("Use a 16–1024 pixel RGBA mask with transparent background")
        pixels = array("f", [0]) * (width * height * 4)
        image.pixels.foreach_get(pixels)
        mask = bytearray(value >= 0.5 for value in pixels[3::4])
        if not any(mask) or all(mask):
            raise ValueError("Reference mask must contain both transparent background and visible subject")
        return width, height, mask
    finally:
        bpy.data.images.remove(image)


def project(scene, cam, matrix, vertices):
    return [world_to_camera_view(scene, cam, matrix @ point) for point in vertices]


def rasterize_triangle(mask, width, height, projected, clip_start, clip_end):
    if any(not clip_start <= point.z <= clip_end for point in projected):
        raise ValueError("Reference geometry crosses the camera clipping depth")
    (ax, ay), (bx, by), (cx, cy) = [(point.x * width, point.y * height) for point in projected]
    abx, aby, acx, acy = bx - ax, by - ay, cx - ax, cy - ay
    det = abx * acy - aby * acx
    if abs(det) < 1e-9:
        return
    lo_x, hi_x = max(0, math.ceil(min(ax, bx, cx) - 0.5)), min(width - 1, math.floor(max(ax, bx, cx) - 0.5))
    lo_y, hi_y = max(0, math.ceil(min(ay, by, cy) - 0.5)), min(height - 1, math.floor(max(ay, by, cy) - 0.5))
    for y in range(lo_y, hi_y + 1):
        v = y + 0.5 - ay
        for x in range(lo_x, hi_x + 1):
            u = x + 0.5 - ax
            beta, gamma = (u * acy - v * acx) / det, (abx * v - aby * u) / det
            if beta >= 0 and gamma >= 0 and beta + gamma <= 1:
                mask[y * width + x] = 1


def silhouette(scene, cam, objects, width, height):
    mask = bytearray(width * height)
    depsgraph = bpy.context.evaluated_depsgraph_get()
    for obj in objects:
        evaluated = obj.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        if mesh is None:
            raise ValueError(f"{obj.name}: reference geometry must evaluate to a mesh")
        try:
            mesh.calc_loop_triangles()
            points = project(scene, cam, evaluated.matrix_world, (vertex.co for vertex in mesh.vertices))
            for triangle in mesh.loop_triangles:
                rasterize_triangle(mask, width, height, [points[i] for i in triangle.vertices],
                                   cam.data.clip_start, cam.data.clip_end)
        finally:
            evaluated.to_mesh_clear()
    return mask


def overlay(name, width, height, expected, observed, landmarks):
    colors = (b"\x19\x23\x34\xff", b"\xf2\x58\x68\xff",
              b"\x25\xc8\xf3\xff", b"\xb8\xc3\xd2\xff")
    pixels = bytearray(b"".join(colors[a + b * 2] for a, b in zip(expected, observed)))

    def cross(center, color, radius):
        if center is None:
            return
        x, y = int(center[0] * width), int((1 - center[1]) * height)
        for offset in range(-radius, radius + 1):
            for col, row in ((x + offset, y), (x, y + offset)):
                if 0 <= col < width and 0 <= row < height:
                    index = (row * width + col) * 4
                    pixels[index:index + 4] = color

    for landmark in landmarks:
        cross(landmark["expected"], b"\xff\xe1\x6b\xff", 5)
        cross(landmark["actual"], b"\x5f\xed\x99\xff", 3)
    packet = png_rgba(width, height, pixels)
    return {"label": f"{name} overlay", "mime_type": "image/png", "bytes": len(packet),
            "dimensions": [width, height], "data": base64.b64encode(packet).decode("ascii")}


def compare(spec):
    scene = bpy.context.scene
    subjects = [scene.objects.get(name) for name in spec["subjects"]]
    missing = [name for name, obj in zip(spec["subjects"], subjects) if obj is None]
    subjects = [obj for obj in subjects if obj and not obj.hide_render]
    missing.extend(name for name in spec["subjects"] if (obj := scene.objects.get(name)) and obj.hide_render)
    reports, images = [], []
    for view in spec["views"]:
        name = view["camera"]
        cam = camera(scene, name, view["role"])
        path = view["mask_path"]
        if file_digest(path) != view["sha256"]:
            raise ValueError(f"{name}: the reference mask changed; register the new specification")
        width, height, expected = read_mask(path)
        # Match the exact aspect used when projecting the evaluated mesh.
        aspect = scene.render.resolution_x * scene.render.pixel_aspect_x / (
            scene.render.resolution_y * scene.render.pixel_aspect_y)
        if abs(width / height - aspect) > 0.01:
            raise ValueError(f"{name}: reference aspect must match the scene's render aspect")
        observed = silhouette(scene, cam, subjects, width, height)
        intersection = sum(a & b for a, b in zip(expected, observed))
        union = sum(a | b for a, b in zip(expected, observed))
        iou = intersection / union
        landmark_results = []
        for landmark in view["landmarks"]:
            obj = scene.objects.get(landmark["object_name"])
            point = world_to_camera_view(scene, cam, obj.matrix_world.translation) if obj and not obj.hide_render else None
            expected_xy, tolerance = landmark["center"], landmark["max_error"]
            actual = [point.x, 1 - point.y] if point and cam.data.clip_start < point.z < cam.data.clip_end else None
            error = math.hypot(actual[0] - expected_xy[0],
                               (actual[1] - expected_xy[1]) * height / width) if actual else None
            landmark_results.append({"name": landmark["object_name"], "expected": expected_xy, "actual": actual,
                                     "error_frame_width": error, "max_error": tolerance,
                                     "status": "missing" if error is None else "match" if error <= tolerance else "shifted"})
        reports.append({"camera": name, "role": view["role"], "silhouette_iou": iou,
                        "minimum_iou": view["minimum_iou"], "landmarks": landmark_results,
                        "needs_edit": iou < view["minimum_iou"] or any(r["status"] != "match" for r in landmark_results)})
        images.append(overlay(name, width, height, expected, observed, landmark_results))
    depth = {"status": "unknown"}
    expected_depth = spec["depth_mm"]
    if expected_depth is not None and subjects and any(view["role"] == "side" for view in reports):
        dep = bpy.context.evaluated_depsgraph_get()
        values = [(evaluated.matrix_world @ Vector(point)).y for obj in subjects
                  for evaluated in (obj.evaluated_get(dep),) for point in evaluated.bound_box]
        mm = (max(values) - min(values)) * scene.unit_settings.scale_length * 1000
        error = abs(mm - expected_depth)
        depth = {"status": "match" if error <= spec["depth_tolerance_mm"] else "out_of_tolerance",
                 "expected_mm": expected_depth, "measured_mm": mm, "error_mm": error,
                 "tolerance_mm": spec["depth_tolerance_mm"], "axis": "world Y"}
    if missing or depth["status"] == "out_of_tolerance" or any(report["needs_edit"] for report in reports):
        status = "needs_edit"
    elif spec["require_depth"] and depth["status"] == "unknown":
        status = "insufficient_data"
    else:
        status = "ready"
    return {"asset_status": status, "missing_subjects": missing, "views": reports,
            "depth": depth, "images": images,
            "overlay_legend": {"red": "reference only", "blue": "evaluated geometry only", "grey": "overlap",
                               "yellow cross": "expected center", "green cross": "observed center"}}
