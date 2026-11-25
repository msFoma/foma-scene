"""Capture the scene with a named camera or the active 3D editor."""
import base64
import hashlib
import json
import struct
import tempfile
import time
import zlib
from pathlib import Path

import bpy


def image_packet(data, label):
    dimensions = list(struct.unpack_from(">II", data, 16))
    return {
        "label": label, "mime_type": "image/png",
        "bytes": len(data), "dimensions": dimensions,
        "data": base64.b64encode(data).decode("ascii"),
    }


def camera_signature(scene, camera, dimensions):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = camera.evaluated_get(depsgraph)
    render, view = scene.render, scene.view_settings
    projection = evaluated.calc_matrix_camera(
        depsgraph, x=dimensions[0], y=dimensions[1],
        scale_x=render.pixel_aspect_x, scale_y=render.pixel_aspect_y)
    state = {
        "camera": camera.session_uid,
        "pose": [list(row) for row in evaluated.matrix_world],
        "projection": [list(row) for row in projection],
        "resolution": [render.resolution_x, render.resolution_y, render.resolution_percentage],
        "pixel_aspect": [render.pixel_aspect_x, render.pixel_aspect_y],
        "frame": [scene.frame_current, scene.frame_subframe],
        "view_layer": bpy.context.view_layer.name,
        "engine": render.engine,
        "transparent": render.film_transparent,
        "border": [render.border_min_x, render.border_min_y, render.border_max_x,
                   render.border_max_y, render.use_crop_to_border] if render.use_border else None,
        "color": [scene.display_settings.display_device, view.view_transform, view.look,
                  view.exposure, view.gamma],
    }
    if render.engine == "CYCLES":
        cycles = scene.cycles
        state["cycles"] = [cycles.samples, cycles.seed, cycles.use_animated_seed,
                           cycles.use_denoising, cycles.use_adaptive_sampling,
                           cycles.adaptive_threshold]
    return hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()


def capture_viewport():
    import gpu

    candidates = [(window, area) for window in bpy.context.window_manager.windows
                  for area in window.screen.areas if area.type == "VIEW_3D"]
    if not candidates:
        raise ValueError("No open 3D Viewport")
    window, area = max(candidates, key=lambda item: item[1].width * item[1].height)
    region = next((region for region in area.regions if region.type == "WINDOW"), None)
    if not region:
        raise ValueError("The 3D Viewport has no visible drawing region")
    region_id, frames = region.as_pointer(), []

    def read_frame():
        current = bpy.context.region
        if frames or current is None or current.as_pointer() != region_id:
            return
        x, y, width, height = gpu.state.viewport_get()
        framebuffer = gpu.state.active_framebuffer_get()
        buffer = framebuffer.read_color(x, y, width, height, 4, 0, "UBYTE")
        buffer.dimensions = width * height * 4
        pixels = bytearray(bytes(buffer))
        pixels[3::4] = b"\xff" * (width * height)
        frames.append((width, height, pixels))

    handle = bpy.types.SpaceView3D.draw_handler_add(read_frame, (), "WINDOW", "POST_PIXEL")
    try:
        area.tag_redraw()
        with bpy.context.temp_override(window=window, area=area, region=region):
            bpy.ops.wm.redraw_timer(type="DRAW_WIN", iterations=1)
    finally:
        bpy.types.SpaceView3D.draw_handler_remove(handle, "WINDOW")
    if not frames:
        raise ValueError("Blender did not draw the requested 3D Viewport")
    return frames[0]


def capture(cameras=(), mode="camera", resolution_percent=None, fast=True):
    started = time.perf_counter()
    if len(cameras) > 6:
        raise ValueError("At most six cameras per observation")
    if resolution_percent is not None and not 10 <= resolution_percent <= 100:
        raise ValueError("resolution_percent must be 10–100")
    if mode == "viewport":
        if bpy.app.background:
            raise ValueError("Viewport requires a Blender window; use camera mode in background")
        frame = capture_viewport()
        capture_ms = (time.perf_counter() - started) * 1000
        packet = image_packet(png_rgba(*frame), "3D Viewport")
        return [packet], "viewport", timings(started, capture_ms)
    with tempfile.TemporaryDirectory(prefix="blender-mcp-") as folder:
        path = Path(folder) / "view.png"
        if mode != "camera":
            raise ValueError("mode must be camera or viewport")
        scene = bpy.context.scene
        render = scene.render
        settings = render.image_settings
        fields = [(scene, "camera"), (render, "filepath"), (render, "use_file_extension"),
                  (render, "resolution_percentage"), (settings, "file_format"),
                  (settings, "color_mode"), (settings, "color_depth"), (settings, "compression")]
        old = [(obj, key, getattr(obj, key)) for obj, key in fields]
        selected = [scene.objects.get(name) for name in cameras] if cameras else [scene.camera]
        if any(cam is None or cam.type != "CAMERA" for cam in selected):
            raise ValueError("Every requested view must name a camera in the current scene")
        images, capture_ms = [], 0
        try:
            render.filepath = str(path)
            render.use_file_extension = True
            render.resolution_percentage = resolution_percent or render.resolution_percentage
            settings.file_format = "PNG"
            settings.color_mode = "RGBA" if scene.render.film_transparent else "RGB"
            settings.color_depth = "8"
            if fast:
                settings.compression = 15
            # Use the full render size for projection. A cropped border changes PNG size only.
            render_size = tuple(size * render.resolution_percentage // 100
                                for size in (render.resolution_x, render.resolution_y))
            for cam in selected:
                scene.camera = cam
                tick = time.perf_counter()
                if "FINISHED" not in bpy.ops.render.render(write_still=True):
                    raise ValueError(f"Render of {cam.name} did not finish")
                capture_ms += (time.perf_counter() - tick) * 1000
                packet = image_packet(path.read_bytes(), cam.name)
                packet["camera_signature"] = camera_signature(scene, cam, render_size)
                images.append(packet)
        finally:
            # JPEG and other formats constrain the allowed channel/depth values.
            for obj, key, value in old:
                setattr(obj, key, value)
        return images, "camera_render", timings(started, capture_ms)


def timings(started, capture_ms):
    elapsed = (time.perf_counter() - started) * 1000
    return {"capture": round(capture_ms, 2), "image_pack": round(max(0, elapsed - capture_ms), 2)}


def png_rgba(width, height, pixels):
    """Small overlay writer, using Blender's RGBA rows in bottom-up order."""
    def chunk(name, data):
        block = name + data
        return len(data).to_bytes(4, "big") + block + zlib.crc32(block).to_bytes(4, "big")

    rows = b"".join(b"\0" + pixels[y * width * 4:(y + 1) * width * 4]
                    for y in reversed(range(height)))
    header = width.to_bytes(4, "big") + height.to_bytes(4, "big") + b"\x08\x06\0\0\0"
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")
