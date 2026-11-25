"""Export selected mesh hierarchy and inspect an actual GLB import in a temporary scene."""
from array import array
import hashlib
import math
import struct
import re
import tempfile
from pathlib import Path

import bpy
from mathutils import Vector

from .layers import layer_nodes


def scalar_or_rgba(value):
    return float(value) if isinstance(value, (float, int)) else list(value)


def texture_image(image):
    pixels = array("f", [0]) * len(image.pixels)
    image.pixels.foreach_get(pixels)
    return {"image_size": list(image.size), "image_name": image.name,
            "pixel_sha256": hashlib.sha256(pixels.tobytes()).hexdigest()}


def triangle_digest(mesh, matrix, scale):
    points = [tuple(round(value * scale * 100_000) for value in matrix @ vertex.co)
              for vertex in mesh.vertices]
    triangles = sorted(tuple(sorted(points[i] for i in face.vertices)) for face in mesh.loop_triangles)
    digest = hashlib.sha256()
    for triangle in triangles:
        digest.update(struct.pack("<9q", *(value for point in triangle for value in point)))
    return digest.hexdigest()


def attributes(material):
    if material is None or not material.use_nodes:
        return None
    tree = material.node_tree
    output = tree.get_output_node("ALL")
    links = output.inputs["Surface"].links if output else ()
    node = links[0].from_node if links else None
    if node is None or node.type != "BSDF_PRINCIPLED":
        return {"needs_bake": ["surface is not a supported Principled BSDF"]}
    result, needs_bake = {}, []
    for name in ("Base Color", "Roughness", "Metallic", "Normal", "Alpha"):
        socket = node.inputs[name]
        if socket.is_linked:
            source = socket.links[0].from_node
            if source.type == "NORMAL_MAP" and source.inputs["Color"].is_linked:
                source = source.inputs["Color"].links[0].from_node
            if source.type == "TEX_IMAGE" and source.image:
                result[name] = texture_image(source.image)
            else:
                needs_bake.append(name)
        elif name != "Normal":
            result[name] = scalar_or_rgba(socket.default_value)
    # Shared Principled channels may be driven by Image Texture, FBX-separator, or exporter internals.
    if layer_nodes(material):
        needs_bake.append("editable Blender MCP image layers")
    return {"channels": result, "needs_bake": sorted(set(needs_bake))}


def snapshot(objects, scale):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    result = {}
    for obj in objects:
        evaluated = obj.evaluated_get(depsgraph)
        matrix = obj.matrix_world
        box = [matrix @ Vector(corner) for corner in evaluated.bound_box] if obj.type == "MESH" else []
        mesh = evaluated.to_mesh() if obj.type == "MESH" else None
        try:
            if mesh:
                mesh.calc_loop_triangles()
            result[obj.name] = {
                "type": obj.type, "parent": obj.parent.name if obj.parent else None,
                "transform": [[value * scale if col == 3 and row < 3 else value
                               for col, value in enumerate(axis)] for row, axis in enumerate(matrix)],
                "bounds_m": [[measure(point[i] for point in box) * scale for i in range(3)]
                             for measure in (min, max)] if box else None,
                "triangles": len(mesh.loop_triangles) if mesh else None,
                "vertices": len(mesh.vertices) if mesh else None,
                "triangle_digest": triangle_digest(mesh, matrix, scale) if mesh else None,
                "materials": [attributes(slot.material) for slot in evaluated.material_slots],
            }
        finally:
            if mesh:
                evaluated.to_mesh_clear()
    return result


def compare(before, imported):
    result, names = [], {}
    for name in before:
        pattern = re.compile(re.escape(name) + r"(?:\.\d+)?$")
        matches = [other for other in imported if pattern.fullmatch(other)]
        if len(matches) == 1:
            names[name] = matches[0]
        else:
            result.append({"object": name, "field": "node mapping", "matches": sorted(matches)})
    for other in imported.keys() - set(names.values()):
        result.append({"object": other, "field": "unexpected imported object"})

    def close(a, b):
        if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
            return len(a) == len(b) and all(close(x, y) for x, y in zip(a, b))
        if isinstance(a, (float, int)) and isinstance(b, (float, int)):
            return math.isclose(a, b, rel_tol=1e-5, abs_tol=1e-5)
        return a == b

    for name, mapped in names.items():
        left, right = before[name], imported[mapped]
        for field in ("type", "transform", "bounds_m", "triangles", "triangle_digest"):
            if not close(left[field], right[field]):
                result.append({"object": name, "field": field, "expected": left[field], "imported": right[field]})
        if names.get(left["parent"]) != right["parent"]:
            result.append({"object": name, "field": "parent", "expected": left["parent"], "imported": right["parent"]})
        if len(left["materials"]) != len(right["materials"]):
            result.append({"object": name, "field": "material count",
                           "expected": len(left["materials"]), "imported": len(right["materials"])})
        for index, (original, actual) in enumerate(zip(left["materials"], right["materials"])):
            if original and actual:
                for channel, expected in original.get("channels", {}).items():
                    got = actual.get("channels", {}).get(channel)
                    same = (isinstance(got, dict) and all(got.get(field) == expected[field] for field in ("image_size", "pixel_sha256"))
                            if isinstance(expected, dict) else close(expected, got))
                    if not same:
                        result.append({"object": name, "field": f"material slot {index} {channel}",
                                       "expected": expected, "imported": got})
            elif original != actual:
                result.append({"object": name, "field": f"material slot {index} presence"})
    return result


def roundtrip(collection, path, overwrite=False):
    context, data = bpy.context, bpy.data
    scene, window = context.scene, context.window
    if context.mode != "OBJECT" or not window:
        raise ValueError("GLB round-trip requires Object Mode and a Blender window context")
    group = next((item for item in [scene.collection, *scene.collection.children_recursive]
                  if item.name == collection), None)
    objects = list(group.all_objects) if group else []
    if not objects or any(obj.type not in {"MESH", "EMPTY"} for obj in objects):
        raise ValueError("Use a nonempty collection containing meshes and optional empties")
    if any((obj.parent and obj.parent not in objects) or not obj.visible_get() for obj in objects):
        raise ValueError("Include every parent and make the collection visible in the active view layer")
    target = Path(path).expanduser().resolve()
    if target.suffix.lower() != ".glb" or not target.parent.is_dir():
        raise ValueError("Supply a .glb path with an existing parent directory")
    if target.exists() and not overwrite:
        raise ValueError("Destination exists; use another path or explicitly allow overwrite")
    active, selected = context.view_layer.objects.active, list(context.selected_objects)
    before = snapshot(objects, scene.unit_settings.scale_length)
    bake = [{"object": name, "slot": index, "effects": material["needs_bake"]}
            for name, entry in before.items() for index, material in enumerate(entry["materials"])
            if material and material["needs_bake"]]
    populations = [data.objects, data.meshes, data.materials, data.images, data.collections,
                   data.node_groups, data.actions]
    prior = [(items, set(items)) for items in populations]
    scratch = None
    with tempfile.TemporaryDirectory(prefix=".blender-glb-", dir=target.parent) as temp:
        output = Path(temp) / target.name
        try:
            for obj in selected:
                obj.select_set(False)
            for obj in objects:
                obj.select_set(True)
            ctx_active = context.view_layer.objects.active
            if ctx_active not in objects:
                context.view_layer.objects.active = objects[0]
            if "FINISHED" not in bpy.ops.export_scene.gltf(filepath=str(output), export_format="GLB",
                    use_selection=True, export_apply=True, export_animations=False):
                raise ValueError("Blender GLB export did not finish")
            scratch = data.scenes.new("MCP GLB verification")
            window.scene = scratch
            if "FINISHED" not in bpy.ops.import_scene.gltf(filepath=str(output)):
                raise ValueError("Blender GLB reimport did not finish")
            context.view_layer.update()
            imported = snapshot(list(scratch.objects), 1)
            differences = compare(before, imported)
            byte_count = output.stat().st_size
            if overwrite:
                output.replace(target)
            else:
                # Hard-link is atomic and fails if a concurrent process created the destination.
                target.hardlink_to(output)
        finally:
            window.scene = scene
            if scratch:
                data.scenes.remove(scratch)
            for items, known in prior:
                for item in set(items) - known:
                    if items == data.objects:
                        items.remove(item, do_unlink=True)
                    elif item.users == 0:
                        items.remove(item)
            for obj in context.selected_objects:
                obj.select_set(False)
            for obj in selected:
                obj.select_set(True)
            context.view_layer.objects.active = active
    return {"path": str(target), "bytes": byte_count, "before": before, "imported": imported,
            "differences": differences, "needs_bake": bake,
            "asset_status": "needs_edit" if differences or bake else "ready"}
