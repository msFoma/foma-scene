"""Editable, physically sized image masks in a target-local planar projection."""
import math
from pathlib import Path

import bpy

LAYER_ID = "blender_mcp_layer"
AXES = {"+" + axis: (index, 1) for index, axis in enumerate("XYZ")}
AXES.update({"-" + axis: (index, -1) for index, axis in enumerate("XYZ")})


def physical_axes(scene, obj):
    matrix = obj.matrix_world.to_3x3()
    lengths = [column.length for column in matrix.col]
    if min(lengths) < 1e-8:
        raise ValueError("Projection requires nonzero object scale")
    directions = [matrix.col[i] / lengths[i] for i in range(3)]
    if any(abs(directions[i].dot(directions[j])) > 1e-4
           for i, j in ((0, 1), (0, 2), (1, 2))):
        raise ValueError("Projection requires orthogonal local axes; the world transform has shear")
    scale = scene.unit_settings.scale_length * 1000
    return [value * scale for value in lengths]


def layer_nodes(material):
    if material is None or not material.use_nodes:
        return []
    return [node for node in material.node_tree.nodes if node.type == "GROUP" and LAYER_ID in node]


def measure(scene, obj, slot=0):
    if not 0 <= slot < len(obj.material_slots):
        raise ValueError("Invalid material slot")
    nodes = layer_nodes(obj.material_slots[slot].material)
    if not nodes:
        return []
    scale = physical_axes(scene, obj)
    result = []
    for node in nodes:
        normal_axis, sign = AXES[node["projection"]]
        u, v = (normal_axis + 1) % 3, (normal_axis + 2) % 3
        baked_scale = node.inputs["Millimetres per unit"].default_value
        center = node.inputs["Center (mm)"].default_value
        result.append({
            "id": node[LAYER_ID], "slot": slot, "projection": node["projection"],
            "width_mm": node.inputs["Width (mm)"].default_value * scale[u] / baked_scale[u],
            "center_u_mm": center[u] * scale[u] / baked_scale[u],
            "center_v_mm": center[v] * scale[v] / baked_scale[v],
            "offset_local_mm": {name: center[i] * scale[i] / baked_scale[i]
                                for i, name in enumerate("xyz") if i != normal_axis},
            "depth_axis_sign": sign,
            "depth_mm": [node.inputs[key].default_value * scale[normal_axis] / baked_scale[normal_axis]
                         for key in ("Min depth (mm)", "Max depth (mm)")],
            "channels": node["channels"].split(","),
            "opacity": node.inputs["Opacity"].default_value,
            "roughness": node.inputs["Finish roughness"].default_value,
            "slot_link": obj.material_slots[slot].link,
        })
    return result


def rgba_inputs(tree, node_type, left, right, output, layer_input):
    node = tree.nodes.new(node_type)
    for target, source in ((node.inputs[1], left), (node.inputs[2], right)):
        tree.links.new(source, target)
    tree.links.new(layer_input, node.inputs[0])
    return node.outputs[output]


def projection_tree(image, projection, channels):
    tree = bpy.data.node_groups.new("MCP image layer", "ShaderNodeTree")
    interface = tree.interface
    fields = [
        ("Base color", "NodeSocketColor"),
        ("Base roughness", "NodeSocketFloat"),
        ("Millimetres per unit", "NodeSocketVector"),
        ("Center (mm)", "NodeSocketVector"),
        ("Width (mm)", "NodeSocketFloat"),
        ("Height (mm)", "NodeSocketFloat"),
        ("Min depth (mm)", "NodeSocketFloat"),
        ("Max depth (mm)", "NodeSocketFloat"),
        ("Opacity", "NodeSocketFloat"),
        ("Finish roughness", "NodeSocketFloat"),
    ]
    for name, kind in fields:
        interface.new_socket(name=name, in_out="INPUT", socket_type=kind)
    for name, kind in fields[:2]:
        interface.new_socket(name=name, in_out="OUTPUT", socket_type=kind)
    nodes, links = tree.nodes, tree.links

    def wire(value, socket):
        if isinstance(value, bpy.types.NodeSocket):
            links.new(value, socket)
        else:
            socket.default_value = value

    def arithmetic(operation, a, b, vector=False):
        node = nodes.new("ShaderNodeVectorMath" if vector else "ShaderNodeMath")
        node.operation = operation
        wire(a, node.inputs[0])
        wire(b, node.inputs[1])
        return node.outputs[0]

    def components(value):
        node = nodes.new("ShaderNodeSeparateXYZ")
        wire(value, node.inputs[0])
        return node.outputs

    inputs = nodes.new("NodeGroupInput").outputs
    outputs = nodes.new("NodeGroupOutput").inputs
    n, sign = AXES[projection]
    u, v = (n + 1) % 3, (n + 2) % 3
    coordinates = nodes.new("ShaderNodeTexCoord").outputs["Object"]
    physical = arithmetic("MULTIPLY", coordinates, inputs["Millimetres per unit"], vector=True)
    position = components(physical)
    relative = components(arithmetic("SUBTRACT", physical, inputs["Center (mm)"], vector=True))
    uv = nodes.new("ShaderNodeCombineXYZ")
    for i, (axis, dimension) in enumerate(((u, "Width (mm)"), (v, "Height (mm)"))):
        coord = relative[axis]
        if i == 0 and sign < 0:
            coord = arithmetic("MULTIPLY", coord, -1)
        normalized = arithmetic("DIVIDE", coord, inputs[dimension])
        wire(arithmetic("ADD", normalized, 0.5), uv.inputs[i])

    texture = nodes.new("ShaderNodeTexImage")
    texture.image = image
    texture.extension = "CLIP"
    links.new(uv.outputs[0], texture.inputs["Vector"])
    normal = nodes.new("ShaderNodeVectorTransform")
    normal.vector_type = "NORMAL"
    normal.convert_from, normal.convert_to = "WORLD", "OBJECT"
    links.new(nodes.new("ShaderNodeNewGeometry").outputs["True Normal"], normal.inputs[0])
    incidence = arithmetic("MULTIPLY", components(normal.outputs[0])[n], sign)
    signed_depth = arithmetic("MULTIPLY", position[n], sign)
    mask = texture.outputs["Alpha"]
    for operation, a, b in (("GREATER_THAN", incidence, 0.05),
                            ("GREATER_THAN", signed_depth, inputs["Min depth (mm)"]),
                            ("LESS_THAN", signed_depth, inputs["Max depth (mm)"])):
        mask = arithmetic("MULTIPLY", mask, arithmetic(operation, a, b))
    mask = arithmetic("MULTIPLY", mask, inputs["Opacity"])
    color, roughness = inputs["Base color"], inputs["Base roughness"]
    if "color" in channels:
        color = rgba_inputs(tree, "ShaderNodeMixRGB", color, texture.outputs["Color"], 0, mask)
    if "roughness" in channels:
        mix = nodes.new("ShaderNodeMix")
        mix.data_type = "FLOAT"
        links.new(mask, mix.inputs[0])
        links.new(roughness, mix.inputs[2])
        links.new(inputs["Finish roughness"], mix.inputs[3])
        roughness = mix.outputs[0]
    links.new(color, outputs["Base color"])
    links.new(roughness, outputs["Base roughness"])
    return tree


def original_input(tree, original, replacement):
    replacement.default_value = original.default_value
    if original.is_linked:
        tree.links.new(original.links[0].from_socket, replacement)


def apply(scene, obj, *, layer_id, image_path=None, width_mm=None, offset_u_mm=0,
          offset_v_mm=0, depth_mm=None, projection="+Z", channels=("color",),
          opacity=None, roughness=None, slot=0, delta_x_mm=None):
    if obj.type != "MESH" or obj.mode != "OBJECT":
        raise ValueError("Select a mesh in Object Mode")
    if not 0 <= slot < len(obj.material_slots):
        raise ValueError("Invalid material slot")
    binding = obj.material_slots[slot]
    original, old_link = binding.material, binding.link
    if original is None or not original.use_nodes:
        raise ValueError("Selected material slot requires a node material")
    previous = next((node for node in layer_nodes(original) if node[LAYER_ID] == layer_id), None)
    scale = physical_axes(scene, obj)
    updating = image_path is None
    if updating:
        if previous is None:
            raise ValueError(f"Layer {layer_id!r} was not found on this slot")
        if delta_x_mm is not None and previous["projection"][-1] == "X":
            raise ValueError("Local X is perpendicular to this layer's projection plane")
    else:
        if previous is not None:
            raise ValueError("Layer already exists. Refine it by using its ID with move_layer.")
        if projection not in AXES or not channels or set(channels) - {"color", "roughness"}:
            raise ValueError("Invalid projection or layer channels")
        if width_mm is None or width_mm <= 0 or not depth_mm or not depth_mm[0] < depth_mm[1]:
            raise ValueError("Provide positive width and an ordered signed depth interval in mm")
        image_path = Path(image_path).expanduser().resolve(strict=True)
        if image_path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"}:
            raise ValueError("Use a supported raster image for the layer")
    values = [offset_u_mm, offset_v_mm, *scale, *(depth_mm or ())]
    optional = [value for value in (opacity, roughness) if value is not None]
    values += optional + [value for value in (width_mm, delta_x_mm) if value is not None]
    if not all(math.isfinite(value) for value in values) or any(not 0 <= value <= 1 for value in optional):
        raise ValueError("Dimensions must be finite and opacity/roughness must be between 0 and 1")

    material = original.copy()
    group = image = None
    try:
        tree = material.node_tree
        if updating:
            layer = tree.nodes[previous.name]
            if delta_x_mm is not None:
                baked_scale = layer.inputs["Millimetres per unit"].default_value
                layer.inputs["Center (mm)"].default_value[0] += delta_x_mm * baked_scale[0] / scale[0]
            if opacity is not None:
                layer.inputs["Opacity"].default_value = opacity
            if roughness is not None:
                if "roughness" not in layer["channels"].split(","):
                    raise ValueError("The layer does not modify roughness")
                layer.inputs["Finish roughness"].default_value = roughness
        else:
            output = tree.get_output_node("ALL")
            surface_links = output.inputs["Surface"].links if output else ()
            shader = surface_links[0].from_node if surface_links else None
            if shader is None or shader.type != "BSDF_PRINCIPLED":
                raise ValueError("Material needs a Principled BSDF linked directly to Surface")
            image = bpy.data.images.load(str(image_path), check_existing=False)
            if min(image.size) == 0:
                raise ValueError("Cannot decode image")
            image.pack()
            group = projection_tree(image, projection, channels)
            layer = tree.nodes.new("ShaderNodeGroup")
            layer.node_tree = group
            layer.name = layer.label = f"MCP layer · {layer_id}"
            layer.location = (shader.location.x - 280, shader.location.y - len(layer_nodes(material)) * 230)
            layer[LAYER_ID], layer["projection"], layer["channels"] = layer_id, projection, ",".join(channels)
            n, _ = AXES[projection]
            center = [0, 0, 0]
            center[(n + 1) % 3], center[(n + 2) % 3] = offset_u_mm, offset_v_mm
            for key, value in {
                "Millimetres per unit": scale, "Center (mm)": center, "Width (mm)": width_mm,
                "Height (mm)": width_mm * image.size[1] / image.size[0],
                "Min depth (mm)": depth_mm[0], "Max depth (mm)": depth_mm[1],
                "Opacity": 1 if opacity is None else opacity,
                "Finish roughness": 0.8 if roughness is None else roughness,
            }.items():
                layer.inputs[key].default_value = value
            for name, shader_input in (("Base color", "Base Color"), ("Base roughness", "Roughness")):
                actual = shader.inputs[shader_input]
                original_input(tree, actual, layer.inputs[name])
                tree.links.new(layer.outputs[name], actual)
        binding.link = "OBJECT"
        binding.material = material
        measured = measure(scene, obj, slot)
    except Exception:
        binding.link = old_link
        binding.material = original
        bpy.data.materials.remove(material)
        if group and group.users == 0:
            bpy.data.node_groups.remove(group)
        if image and image.users == 0:
            bpy.data.images.remove(image)
        raise

    def undo():
        if binding.link != "OBJECT" or binding.material != material:
            raise ValueError("The target material slot was manually edited")
        if old_link == "DATA" and obj.data.materials[slot] != original:
            raise ValueError("The source mesh material slot was manually edited")
        binding.material = original
        binding.link = old_link
        if material.users == 0:
            bpy.data.materials.remove(material)
        if group and group.users == 0:
            bpy.data.node_groups.remove(group)
        if image and image.users == 0:
            bpy.data.images.remove(image)

    return measured, undo
