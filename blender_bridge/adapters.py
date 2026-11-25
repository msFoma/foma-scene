"""Known operator contracts. Discovery and activation use Blender's installed add-ons."""
from pathlib import Path

import addon_utils
import bpy

OPERATORS = {
    "svg": ("import_curve.svg",),
    "bool_tool": ("btool.boolean_diff",),
    "node_wrangler": ("node.nw_add_texture",),
}


def category(module):
    short = module.__name__.split(".")[-1]
    if short in {"bool_tool", "object_boolean_tools"}:
        return "bool_tool"
    if short == "io_curve_svg":
        return "svg"
    return short


def operator(name):
    namespace, method = name.split(".")
    candidate = getattr(getattr(bpy.ops, namespace), method)
    try:
        candidate.get_rna_type()
    except (KeyError, RuntimeError):
        return None
    return candidate


def inventory(refresh=False):
    items, known = [], set()
    for module in addon_utils.modules(refresh=refresh):
        name = module.__name__
        info = addon_utils.module_bl_info(module)
        group = category(module)
        known.add(group)
        enabled = addon_utils.check(name)[1]
        registered = [name for name in OPERATORS.get(group, ()) if operator(name)]
        items.append({"module": name, "name": info.get("name", name), "adapter": group if group in OPERATORS else None,
                      "version": list(info.get("version", ())), "state": "active" if enabled else "installed",
                      "registered": registered if enabled else []})
    items.extend({"adapter": group, "state": "missing", "registered": []} for group in OPERATORS.keys() - known)
    return sorted(items, key=lambda item: item.get("module", item["adapter"]))


def enable(module):
    installed = next((item for item in addon_utils.modules(refresh=True) if item.__name__ == module), None)
    if installed is None:
        raise ValueError("Add-on is not installed in this Blender profile")
    info = addon_utils.module_bl_info(installed)
    minimum = tuple(info.get("blender", ()))
    if minimum and bpy.app.version < minimum:
        raise ValueError(f"Add-on requires Blender {'.'.join(map(str, minimum))}")
    if not addon_utils.check(module)[1]:
        loaded = addon_utils.enable(module, default_set=True, persistent=True)
        if loaded is None or not addon_utils.check(module)[1]:
            raise ValueError("Blender could not register the add-on. Inspect Blender's console.")
    return {"addon": next(item for item in inventory() if item.get("module") == module)}


def run_operator(name, adapter, **kwargs):
    if not any(item.get("adapter") == adapter and item["state"] == "active" for item in inventory()):
        raise ValueError(f"Activate the installed {adapter} add-on first")
    op = operator(name)
    if op is None:
        raise ValueError(f"Installed add-on does not register the supported operator {name}")
    if not op.poll():
        raise ValueError(f"{name}: poll() rejected the current editor, selection or mode")
    if "FINISHED" not in op("EXEC_DEFAULT", **kwargs):
        raise ValueError(f"{name} did not finish")


def svg_import(path):
    path = Path(path).expanduser().resolve(strict=True)
    if path.suffix.lower() != ".svg" or not path.is_file():
        raise ValueError("Supply a local .svg file")
    if bpy.context.mode != "OBJECT":
        raise ValueError("Import SVG in Object Mode")
    before = set(bpy.data.objects)
    collections = set(bpy.data.collections)
    curves = set(bpy.data.curves)
    materials = set(bpy.data.materials)
    active = bpy.context.view_layer.objects.active
    selected = list(bpy.context.selected_objects)

    def undo():
        for obj in set(bpy.data.objects) - before if created is None else created:
            bpy.data.objects.remove(obj, do_unlink=True)
        for collection in (set(bpy.data.collections) - collections if created_collections is None else created_collections):
            if not collection.objects and not collection.children:
                bpy.data.collections.remove(collection)
        for source, old in ((bpy.data.curves, curves), (bpy.data.materials, materials)):
            for item in set(source) - old:
                if item.users == 0 and item in (created_data if created is not None else set(source)):
                    source.remove(item)
        for obj in bpy.context.selected_objects:
            obj.select_set(False)
        for obj in selected:
            if obj.name in bpy.context.view_layer.objects:
                obj.select_set(True)
        bpy.context.view_layer.objects.active = active

    created = created_collections = None
    created_data = set()
    try:
        run_operator("import_curve.svg", "svg", filepath=str(path))
        added = set(bpy.data.objects) - before
        if not added or any(obj.type != "CURVE" for obj in added):
            raise ValueError("SVG Import produced no editable curve objects")
        created_collections = set(bpy.data.collections) - collections
        created_data = (set(bpy.data.curves) - curves) | (set(bpy.data.materials) - materials)
        created = added
    except Exception:
        undo()
        raise
    return sorted(created, key=lambda obj: obj.name), undo


def bool_difference(body, cutter, stack_index):
    if body == cutter or body.type != "MESH" or cutter.type != "MESH" or bpy.context.mode != "OBJECT":
        raise ValueError("Bool Tool requires two distinct meshes in Object Mode")
    if set(bpy.context.selected_objects) != {body, cutter}:
        raise ValueError("Select exactly the active body and the named cutter")
    originals = list(body.modifiers)
    if stack_index is not None and not 0 <= stack_index <= len(originals):
        raise ValueError("Invalid modifier stack index")
    if any(mod.type == "BOOLEAN" and mod.object == cutter for mod in originals):
        raise ValueError("A Boolean modifier already uses this cutter")
    states = [(obj, attr, getattr(obj, attr)) for obj, attrs in (
        (cutter, ("hide_render", "display_type", "visible_camera", "visible_diffuse", "visible_glossy",
                  "visible_shadow", "visible_transmission", "visible_volume_scatter")),
    ) for attr in attrs]
    props = [(obj, name, obj.get(name)) for obj, name in (
        (body, "BoolToolRoot"), (cutter, "BoolToolBrush"), (cutter, "BoolTool_FTransform"))]

    def undo():
        for mod in list(body.modifiers):
            if mod not in originals and mod.type == "BOOLEAN" and mod.object == cutter:
                body.modifiers.remove(mod)
        for obj, attr, value in states:
            setattr(obj, attr, value)
        for obj, name, value in props:
            if value is not None:
                obj[name] = value
            elif name in obj:
                del obj[name]
        bpy.context.view_layer.update()

    try:
        run_operator("btool.boolean_diff", "bool_tool")
        created = [mod for mod in body.modifiers if mod not in originals]
        if len(created) != 1 or created[0].type != "BOOLEAN" or created[0].object != cutter:
            raise ValueError("Bool Tool did not create the expected editable brush modifier")
        mod = created[0]
        if stack_index is not None:
            body.modifiers.move(len(body.modifiers) - 1, stack_index)
        bpy.context.view_layer.update()
        evaluated = body.evaluated_get(bpy.context.evaluated_depsgraph_get())
        measurement = {"name": mod.name, "type": mod.type, "operation": mod.operation,
                       "stack_index": list(body.modifiers).index(mod),
                       "base_faces": len(body.data.polygons), "evaluated_faces": len(evaluated.data.polygons)}
    except Exception:
        undo()
        raise
    return measurement, undo


def node_texture(obj, node_name, slot, area_type):
    if not 0 <= slot < len(obj.material_slots):
        raise ValueError("Invalid material slot")
    binding = obj.material_slots[slot]
    original, original_link = binding.material, binding.link
    if not original or not original.use_nodes:
        raise ValueError("Active object needs a node material")
    window = bpy.context.window
    if bpy.app.background or not window:
        raise ValueError("Node Wrangler needs a Blender GUI Shader Editor")
    areas = [area for area in window.screen.areas if
             (area.type == "NODE_EDITOR" and area.ui_type == "ShaderNodeTree") or area.type == "VIEW_3D"]
    desired = "NODE_EDITOR" if area_type == "shader_editor" else "VIEW_3D"
    area = next((area for area in areas if area.type == desired), None)
    if not area:
        raise ValueError(f"Open a {area_type} in the active workspace")
    space = area.spaces.active
    if desired == "NODE_EDITOR" and (space.pin or space.shader_type != "OBJECT" or len(space.path) > 1):
        raise ValueError("Use an unpinned Object Shader Editor at the material root")
    region = next(region for region in area.regions if region.type == "WINDOW")
    material = original.copy()
    original_slot = obj.active_material_index
    original_tree = space.node_tree if desired == "NODE_EDITOR" else None

    def undo():
        binding.material = original
        binding.link = original_link

    try:
        binding.link, binding.material = "OBJECT", material
        obj.active_material_index = slot
        tree = material.node_tree
        node = tree.nodes.get(node_name)
        if node is None:
            raise ValueError("Requested Shader Editor node does not exist")
        for candidate in tree.nodes:
            candidate.select = candidate == node
        tree.nodes.active = node
        names = set(tree.nodes.keys())
        with bpy.context.temp_override(window=window, area=area, region=region):
            if desired == "NODE_EDITOR":
                # The UI normally updates the node tree on its next redraw.
                space.node_tree = tree
                if space.edit_tree != tree:
                    space.path.start(tree)
                if space.edit_tree != tree:
                    raise ValueError("Could not set the Shader Editor to the active material")
            run_operator("node.nw_add_texture", "node_wrangler")
        added = sorted(({"name": node.name, "type": node.type} for node in tree.nodes
                        if node.name not in names), key=lambda node: node["name"])
        if not added:
            raise ValueError("Node Wrangler made no changes")
        return added, undo
    except Exception:
        undo()
        if desired == "NODE_EDITOR":
            space.node_tree = original_tree
        bpy.data.materials.remove(material)
        raise
    finally:
        obj.active_material_index = original_slot
