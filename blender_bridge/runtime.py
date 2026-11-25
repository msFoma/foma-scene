"""Operations scoped to the active Blender file and main thread."""
import hashlib
import json
import tempfile
import time
from array import array
from collections import deque
from itertools import islice
from pathlib import Path
from uuid import uuid4

import bpy

from . import adapters, gears, glb, layers, observation, reference


def key(datablock):
    return getattr(datablock, "session_uid", datablock.as_pointer())


def object_info(obj):
    return {
        "id": key(obj), "name": obj.name, "type": obj.type, "mode": obj.mode,
        "dimensions_blender_units": list(obj.dimensions),
        "materials": [{"name": slot.material.name, "id": key(slot.material), "link": slot.link}
                      if slot.material else None for slot in obj.material_slots],
    }


def mesh_state(mesh):
    digest = hashlib.blake2s()
    for elements, field, kind, width in (
        (mesh.vertices, "co", "f", 3),
        (mesh.edges, "vertices", "i", 2),
        (mesh.loops, "vertex_index", "i", 1),
        (mesh.polygons, "loop_total", "i", 1),
        (mesh.polygons, "material_index", "i", 1),
    ):
        values = array(kind, [0]) * (len(elements) * width)
        elements.foreach_get(field, values)
        digest.update(len(elements).to_bytes(8, "little"))
        digest.update(values)
    return digest.hexdigest()


def curve_state(curve):
    return [(spline.type, [list(point.co) for point in spline.points],
             [[list(p.co), list(p.handle_left), list(p.handle_right)] for p in spline.bezier_points],
             spline.use_cyclic_u) for spline in curve.splines]


def socket_state(socket):
    value = getattr(socket, "default_value", None)
    return socket.identifier, (list(value) if value is not None and socket.type in {"RGBA", "VECTOR"} else repr(value))


def graph_signature(tree, shared):
    if tree is None:
        return None
    if tree not in shared:
        nodes = []
        for node in tree.nodes:
            image = getattr(node, "image", None)
            group = getattr(node, "node_tree", None)
            layer = ([repr(node.get(prop)) for prop in (layers.LAYER_ID, "projection", "channels")]
                     if layers.LAYER_ID in node else None)
            nodes.append([node.name, node.type, [socket_state(socket) for socket in node.inputs],
                          layer, [getattr(node, field, None) for field in
                                  ("operation", "blend_type", "data_type", "extension", "interpolation", "projection")],
                          [key(image), image.filepath, image.colorspace_settings.name] if image else None,
                          [key(group), graph_signature(group, shared)] if group else None])
        links = [(link.from_node.name, link.from_socket.identifier, link.to_node.name,
                  link.to_socket.identifier) for link in tree.links]
        shared[tree] = hashlib.blake2s(json.dumps([nodes, links], sort_keys=True).encode()).hexdigest()
    return shared[tree]


class Runtime:
    def __init__(self):
        self.history = deque(maxlen=32)
        self.session_id = uuid4().hex
        self.revision = 0
        self.reference_specs = {}
        self.last_message = "Bridge ready"

    def file_loaded(self, *_):
        self.__init__()

    def pose_state(self, obj, shared=None):
        # A shared datablock is read once per synchronous inspection. Later requests always reread it.
        if shared is None:
            shared = {}
        materials = []
        for slot in obj.material_slots:
            material = slot.material
            signature = graph_signature(material.node_tree, shared) if material and material.use_nodes else None
            materials.append((key(material) if material else None, slot.link, signature))
        geometry = None
        if obj.type in {"MESH", "CURVE"}:
            if obj.data not in shared:
                shared[obj.data] = mesh_state(obj.data) if obj.type == "MESH" else curve_state(obj.data)
            geometry = shared[obj.data]
        modifiers = [(mod.name, mod.type, mod.show_viewport, mod.show_render,
                      [(prop.identifier, repr(getattr(mod, prop.identifier)))
                       for prop in mod.bl_rna.properties
                       if not prop.is_readonly and prop.identifier not in ("name",)])
                     for mod in obj.modifiers]
        return [obj.name, obj.hide_render, obj.display_type, sorted(c.name for c in obj.users_collection),
                [obj.get(name) for name in ("BoolToolRoot", "BoolToolBrush", "BoolTool_FTransform")],
                key(obj.data) if obj.data else None, [list(row) for row in obj.matrix_world],
                materials, geometry, modifiers]

    def token_scope(self):
        context = bpy.context
        active = context.view_layer.objects.active
        state = [self.session_id, self.revision, bpy.data.filepath, key(context.scene),
                 context.scene.unit_settings.scale_length, context.scene.frame_current, context.scene.frame_subframe,
                 context.view_layer.name, context.mode, key(active) if active else None,
                 sorted(key(obj) for obj in context.selected_objects)]
        return hashlib.sha256(json.dumps(state, sort_keys=True).encode())

    def token(self, objects=(), *, scope=None, shared=None):
        digest = scope.copy() if scope is not None else self.token_scope()
        states = [(key(obj), self.pose_state(obj, shared)) for obj in objects]
        digest.update(json.dumps(states, sort_keys=True).encode())
        return digest.hexdigest()

    def resolve(self, object_id):
        active = bpy.context.view_layer.objects.active
        if active and key(active) == object_id:
            return active
        obj = next((obj for obj in bpy.context.scene.objects if key(obj) == object_id), None)
        if obj is None:
            raise ValueError("Object ID is unavailable in the current scene")
        return obj

    def assert_target(self, object_id, revision_token):
        obj = self.resolve(object_id)
        if self.token([obj]) != revision_token:
            raise ValueError("Stale revision, selection, or target. Inspect the current scene before writing.")
        if bpy.context.view_layer.objects.active != obj or not obj.select_get():
            raise ValueError("Target must be the active selected object")
        return obj

    def describe(self):
        scene, active = bpy.context.scene, bpy.context.view_layer.objects.active
        return {"session_id": self.session_id, "revision": self.revision, "file": bpy.data.filepath,
                "scene": scene.name, "frame": scene.frame_current,
                "blender_version": bpy.app.version_string, "background": bpy.app.background,
                "mode": bpy.context.mode, "active_object_id": key(active) if active else None,
                "selected_ids": [key(obj) for obj in bpy.context.selected_objects],
                "scene_token": self.token()}

    def images(self, view):
        images, source, timing = observation.capture(**view)
        return {"images": images, "source": source, "timing_ms": timing}

    def inspect(self, object_id=None, full=False, collection=None, offset=0, limit=30, detail="edit"):
        context = bpy.context
        if not 0 <= offset or not 1 <= limit <= 200:
            raise ValueError("Use offset ≥ 0 and limit between 1 and 200")
        if detail not in {"edit", "summary"}:
            raise ValueError("Choose edit or summary detail")
        if object_id is not None:
            objects = [self.resolve(object_id)]
        elif collection is not None:
            allowed = list(context.scene.collection.children_recursive) + [context.scene.collection]
            group = next((coll for coll in allowed if coll.name == collection), None)
            if not group:
                raise ValueError("Collection is unavailable in the current scene")
            objects = group.all_objects
        elif full:
            objects = context.scene.objects
        else:
            objects = context.selected_objects
        page = list(islice(objects, offset, offset + limit))
        inspected = [object_info(obj) for obj in page]
        if detail == "edit":
            scope, shared = self.token_scope(), {}
            for obj, entry in zip(page, inspected, strict=True):
                entry.update(revision_token=self.token([obj], scope=scope, shared=shared),
                             layers=[layer for slot in range(len(obj.material_slots))
                                     for layer in layers.measure(context.scene, obj, slot)])
        return {
            "objects": inspected, "detail": detail, "total": len(objects), "offset": offset, "limit": limit,
            "cameras": [obj.name for obj in context.scene.objects if obj.type == "CAMERA"],
        }

    def observe(self, object_id=None, mode="camera", cameras=(), resolution_percent=None, fast=True):
        result = self.images(dict(mode=mode, cameras=cameras, resolution_percent=resolution_percent, fast=fast))
        if object_id is not None:
            result.update(self.inspect(object_id=object_id))
        return result

    def record(self, obj, undo, action, guarded=None, scene_scope=False):
        targets = list(guarded) if guarded is not None else [obj]
        self.history.append((None if scene_scope else key(obj), action, undo,
                             [(target, self.pose_state(target)) for target in targets]))
        self.revision += 1
        self.last_message = action

    def change_layer(self, object_id, revision_token, layer_id, view=None, **kwargs):
        if not layer_id or len(layer_id) > 80:
            raise ValueError("Layer ID must have 1–80 characters")
        obj = self.assert_target(object_id, revision_token)
        tick = time.perf_counter()
        measured, undo = layers.apply(bpy.context.scene, obj, layer_id=layer_id, **kwargs)
        command_ms = round((time.perf_counter() - tick) * 1000, 2)
        try:
            result = self.images(view) if view is not None else {}
            self.record(obj, undo, f"Layer {layer_id} updated on {obj.name}")
        except Exception:
            undo()
            raise
        result.setdefault("timing_ms", {})["operation"] = command_ms
        result.update({"layers": measured, "object": object_info(obj),
                       "revision_token": self.token([obj])})
        return result

    def import_svg(self, scene_token, path):
        if self.token() != scene_token:
            raise ValueError("Stale scene or selection")
        objects, undo = adapters.svg_import(path)
        try:
            self.record(objects[0], undo, "Imported SVG", guarded=objects, scene_scope=True)
        except Exception:
            undo()
            raise
        return {"operator": "import_curve.svg", "objects": [object_info(obj) for obj in objects]}

    def create_gear_train(self, scene_token, **params):
        if self.token() != scene_token:
            raise ValueError("Stale scene or selection")
        if bpy.context.mode != "OBJECT":
            raise ValueError("Create a gear train in Object Mode")
        assembly, wheels, report = gears.build(bpy.context.scene, **params)
        try:
            self.record(wheels[0], assembly.undo, "Created spur gear train",
                        guarded=assembly.objects, scene_scope=True)
        except Exception:
            assembly.undo()
            raise
        return {**report, "collection": assembly.collection.name,
                "gears": [{**object_info(wheel), "teeth": wheel["teeth"],
                           "pitch_diameter_mm": wheel["pitch_diameter_mm"],
                           "speed_relative_to_input": wheel["speed_relative_to_input"]} for wheel in wheels]}

    def bool_difference(self, object_id, revision_token, cutter_id, cutter_token, stack_index=None, view=None):
        obj = self.assert_target(object_id, revision_token)
        cutter = self.resolve(cutter_id)
        if self.token([cutter]) != cutter_token:
            raise ValueError("Stale cutter")
        info, undo = adapters.bool_difference(obj, cutter, stack_index)
        try:
            result = self.images(view) if view is not None else {}
            self.record(obj, undo, f"Bool Tool brush on {obj.name}", guarded=[obj, cutter])
        except Exception:
            undo()
            raise
        return {**result, "operator": "btool.boolean_diff", "modifier": info,
                "body": object_info(obj), "cutter": object_info(cutter)}

    def node_texture(self, object_id, revision_token, node_name, slot=0, area_type="shader_editor"):
        obj = self.assert_target(object_id, revision_token)
        nodes, undo = adapters.node_texture(obj, node_name, slot, area_type)
        try:
            self.record(obj, undo, f"Node Wrangler texture on {obj.name}")
        except Exception:
            undo()
            raise
        return {"operator": "node.nw_add_texture", "nodes": nodes, "context": area_type}

    def undo_last(self, revision_token, object_id, steps=1):
        self.assert_target(object_id, revision_token)
        if not 1 <= steps <= len(self.history):
            raise ValueError(f"Choose 1–{len(self.history)} available MCP edits")
        undone = []
        stopped = None
        for _ in range(steps):
            scope, action, undo, guards = self.history[-1]
            try:
                if scope is not None and scope != object_id:
                    raise ValueError("The last edit belongs to a different active object")
                if any(self.pose_state(obj) != expected for obj, expected in guards):
                    raise ValueError("A target changed after the MCP edit")
                undo()
            except (ValueError, RuntimeError, ReferenceError) as exc:
                if not undone:
                    raise ValueError(f"Rollback stopped: {exc}") from exc
                stopped = str(exc)
                break
            self.history.pop()
            self.revision += 1
            undone.append(action)
        self.last_message = f"Rolled back {len(undone)} MCP edit(s)"
        # A scene-level rollback can remove the object used to authorize the operation.
        try:
            target = self.resolve(object_id)
        except ValueError:
            target = None
        result = {"undone": undone, "remaining": len(self.history),
                  "revision_token": self.token([target]) if target else None}
        if stopped is not None:
            result["stopped"] = stopped
        return result

    def set_reference(self, scene_token, spec_id, spec):
        if self.token() != scene_token:
            raise ValueError("Stale scene or selection")
        if spec_id not in self.reference_specs and len(self.reference_specs) >= 16:
            raise ValueError("Reference catalog is full; reuse a specification ID")
        self.reference_specs[spec_id] = (key(bpy.context.scene), reference.validate(spec))
        self.revision += 1
        return {"spec_id": spec_id, "cameras": [view["camera"] for view in spec["views"]]}

    def compare_reference(self, spec_id):
        entry = self.reference_specs.get(spec_id)
        if entry is None or entry[0] != key(bpy.context.scene):
            raise ValueError("No reference specification with this ID in the current scene")
        return {"spec_id": spec_id, **reference.compare(entry[1])}

    def checkpoint(self, scene_token, path):
        if self.token() != scene_token:
            raise ValueError("Stale scene or selection")
        destination = Path(path).expanduser().absolute()
        if destination.suffix.lower() != ".blend" or not destination.parent.is_dir() or destination.exists():
            raise ValueError("Supply a new .blend path with an existing parent directory")
        # Blender remaps relative assets against the staging file's directory.
        with tempfile.NamedTemporaryFile(prefix=".blender-mcp-", suffix=".blend",
                                         dir=destination.parent, delete=False) as file:
            staged = Path(file.name)
        try:
            if "FINISHED" not in bpy.ops.wm.save_as_mainfile(filepath=str(staged), copy=True,
                    compress=False, relative_remap=bool(bpy.data.filepath), check_existing=False):
                raise ValueError("Blender could not save the scene copy")
            destination.hardlink_to(staged)
        finally:
            for filename in (staged, staged.with_suffix(".blend1"), staged.with_suffix(".blend@")):
                filename.unlink(missing_ok=True)
        return {"path": str(destination.resolve()), "bytes": destination.stat().st_size}

    def export_glb(self, scene_token, collection, path, overwrite=False):
        if self.token() != scene_token:
            raise ValueError("Stale scene or selection")
        return glb.roundtrip(collection, path, overwrite)

    def dispatch(self, action, args):
        methods = {
            "inspect": self.inspect, "observe": self.observe,
            "apply_layer": self.change_layer, "move_layer": self.change_layer, "update_layer": self.change_layer,
            "rollback": self.undo_last, "list_addons": lambda refresh=False: {"addons": adapters.inventory(refresh)},
            "enable_addon": adapters.enable, "import_svg": self.import_svg,
            "create_gear_train": self.create_gear_train,
            "bool_difference": self.bool_difference, "node_texture": self.node_texture,
            "set_reference": self.set_reference, "compare_reference": self.compare_reference,
            "export_glb": self.export_glb, "checkpoint": self.checkpoint,
        }
        if action not in methods:
            raise ValueError("Unknown Blender operation")
        return {**methods[action](**args), **self.describe()}
