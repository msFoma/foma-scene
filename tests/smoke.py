"""Exercise real MCP clients against one persistent Blender, then compare decoded pixels."""
import argparse
import asyncio
import base64
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path

from mcp import ClientSession, StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client
from PIL import Image, ImageChops, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def logo(path):
    image = Image.new("RGBA", (480, 180))
    draw = ImageDraw.Draw(image)
    color = (255, 156, 69, 255)
    draw.polygon([(0, 84), (94, 0), (178, 0), (64, 101), (131, 101),
                  (43, 179), (0, 179), (65, 115), (0, 115)], fill=color)
    font = ImageFont.truetype("DejaVuSans-Bold.ttf", 68)
    draw.text((174, 45), "VECTOR", font=font, fill=color, stroke_width=0)
    draw.rectangle((181, 138, 479, 144), fill=color)
    image.save(path)


@asynccontextmanager
async def session(transport, env, endpoint=None):
    scope = (stdio_client(StdioServerParameters(command=sys.executable,
             args=["-m", "blender_mcp.server"], env=env, cwd=str(ROOT)))
             if transport == "stdio" else streamable_http_client(endpoint))
    async with scope as connection:
        async with ClientSession(*connection[:2]) as client:
            init = await client.initialize()
            assert init.serverInfo.name == "Blender MCP"
            yield client


async def call(client, tool, args=None, out=None, label=None, allow_error=False):
    tick = time.perf_counter()
    response = await client.call_tool(tool, args or {})
    client_wall = (time.perf_counter() - tick) * 1000
    assert not response.isError, response
    data = json.loads(response.content[0].text)
    if not allow_error:
        assert data.get("state") == "succeeded", data
    data["client_wall_ms"] = round(client_wall, 2)
    server_wall = data.get("mcp_timing_ms", {}).get("server_wall")
    if server_wall is not None:
        data["mcp_receive_overhead_ms"] = round(max(0, client_wall - server_wall), 2)
    images = response.content[1:]
    assert len(images) == len(data.get("image_metadata", []))
    for index, (part, metadata) in enumerate(zip(images, data.get("image_metadata", []))):
        assert part.type == "image" and part.mimeType == "image/png"
        pixels = base64.b64decode(part.data)
        assert len(pixels) == metadata["bytes"]
        if out:
            name = metadata["label"].lower().replace(" ", "-")
            path = out / f"{label}-{index}-{name}.png"
            path.write_bytes(pixels)
            metadata["file"] = path.name
            with Image.open(path) as image:
                image.load()
                if metadata["dimensions"] is not None:
                    assert list(image.size) == metadata["dimensions"]
    return data


async def readiness(process, path):
    for _ in range(300):
        if path.exists():
            return
        assert process.poll() is None, "Blender exited. See blender.log."
        await asyncio.sleep(0.10)
    raise RuntimeError("Blender did not start")


async def manual(out, op):
    pending, done = out / "manual.pending", out / "manual.done"
    done.unlink(missing_ok=True)
    temp = pending.with_suffix(".tmp")
    temp.write_text(op)
    temp.replace(pending)
    for _ in range(200):
        if done.exists():
            assert done.read_text() == op, done.read_text()
            return
        await asyncio.sleep(0.05)
    raise RuntimeError(f"Blender UI fixture did not acknowledge {op}")


def contact_sheet(out):
    sheet = Image.new("RGB", (1416, 910), "#151c24")
    font = ImageFont.truetype("DejaVuSans-Bold.ttf", 20)
    draw = ImageDraw.Draw(sheet)
    for i, (filename, title) in enumerate((
        ("before-0-top.png", "Before · shared material"),
        ("moved-0-top.png", "After · 40 mm logo moved +3 mm on local X"),
        ("moved-1-underside.png", "Underside · zero changed pixels"),
        ("moved-2-close-up.png", "Close-up · editable matte layer"),
    )):
        x, y = 8 + (i % 2) * 704, 8 + (i // 2) * 450
        draw.text((x, y), title, font=font, fill="white")
        with Image.open(out / filename) as frame:
            sheet.paste(frame, (x, y + 25))
    sheet.save(out / "before-after.png", optimize=True)


async def verify_lids(stdio, http, out, before, moved, focused):
    checks = [
        {"camera": "Top", "regions": [
            {"name": "Selected lid", "box": [0, 0, 0.5, 1], "min_changed_pixels": 100},
            {"name": "Neighbor", "box": [360 / 700, 0, 1, 1], "max_changed_pixels": 0},
        ]},
        {"camera": "Underside", "regions": [{"name": "Entire underside", "max_changed_pixels": 0}]},
    ]
    args = dict(before_job_id=before["job_id"], after_job_id=moved["job_id"], checks=checks)
    verified = await call(http, "verify_edit", args, out, "verification")
    assert verified["passed"] and verified["new_renders"] == 0
    assert verified["before"]["revision"] == before["revision"]
    assert verified["after"]["revision"] == moved["revision"]
    top, bottom = verified["views"]
    assert top["regions"][1]["changed_pixels"] == bottom["regions"][0]["changed_pixels"] == 0
    assert bottom["changed_bounds_px"] is None
    left = out / before["image_metadata"][0]["file"]
    right = out / moved["image_metadata"][0]["file"]
    with Image.open(left) as a, Image.open(right) as b:
        delta = ImageChops.difference(a.convert("RGB"), b.convert("RGB"))
        rgb = delta.tobytes()
        independent_count = sum(rgb[i:i + 3] != b"\0\0\0" for i in range(0, len(rgb), 3))
        assert top["changed_pixels"] == independent_count
        assert top["changed_bounds_px"] == list(delta.getbbox())

    numbers_only = await call(stdio, "verify_edit", {**args, "include_overlays": False})
    assert numbers_only["views"] == verified["views"] and "image_metadata" not in numbers_only

    failure = await call(http, "verify_edit", {**args, "include_overlays": False, "checks": [
        {"camera": "Top", "regions": [{"name": "Protect the full top", "max_changed_pixels": 0}]}
    ]})
    assert not failure["passed"] and not failure["views"][0]["regions"][0]["passed"]

    mismatch = await call(stdio, "verify_edit", {**args, "before_job_id": focused["job_id"],
                         "checks": checks[:1]}, allow_error=True)
    assert mismatch["state"] == "failed" and "matching camera" in mismatch["error"]
    missing = await call(http, "verify_edit", {**args, "before_job_id": "missing-frame"},
                         allow_error=True)
    assert missing["state"] == "failed" and "no retained completed result" in missing["error"]

    baseline = await call(http, "observe", {"view": {"cameras": ["Top", "Underside"]}})
    await manual(out, "shift-top-camera")
    try:
        shifted = await call(http, "observe", {"view": {"cameras": ["Top"]}})
    finally:
        await manual(out, "restore-top-camera")
    assert shifted["image_metadata"][0]["dimensions"] == moved["image_metadata"][0]["dimensions"]
    shifted_camera = await call(http, "verify_edit", {**args, "after_job_id": shifted["job_id"],
                                "checks": checks[:1]}, allow_error=True)
    assert shifted_camera["state"] == "failed" and "matching camera projection" in shifted_camera["error"]
    return {"verified": verified, "numbers_only": numbers_only, "wrong_scope": failure,
            "mismatched_settings": mismatch, "missing_frames": missing,
            "shifted_camera": shifted_camera, "fresh_two_camera_render": baseline}


async def run_lids(stdio, http, out):
    assert {item.name for item in (await stdio.list_tools()).tools} == {
        item.name for item in (await http.list_tools()).tools}
    compact = await call(stdio, "inspect_scene")
    full = await call(stdio, "inspect_scene", {"full": True})
    target = compact["objects"][0]
    neighbor = next(obj for obj in full["objects"] if obj["name"].startswith("Lid B"))
    assert target["materials"] == neighbor["materials"]
    assert target["layers"] == neighbor["layers"] == []
    view = {"cameras": ["Top", "Underside", "Close-up"]}
    art = out / "logo.png"
    logo(art)
    before = await call(stdio, "observe", {"view": {**view, "fast": False}}, out, "before")
    fast = await call(http, "observe", {"view": view}, out, "before-fast")
    focused = await call(http, "observe", {"view": {"cameras": ["Top"], "resolution_percent": 50}}, out, "focused")
    assert focused["image_metadata"][0]["dimensions"] == [350, 210]
    apply_args = dict(object_id=target["id"], revision_token=target["revision_token"],
                      layer_id="vector-logo", job_id="lids-apply", image_path=str(art), width_mm=40,
                      depth_mm=[4.2, 9.2], channels=["color", "roughness"], roughness=0.78, view=view)
    placed = await call(stdio, "apply_layer", apply_args, out, "placed")
    move_args = dict(object_id=target["id"], revision_token=placed["revision_token"],
                     layer_id="vector-logo", job_id="lids-move", delta_x_mm=3, view=view)
    moved = await call(http, "move_layer", move_args, out, "moved")
    retrieved = await call(stdio, "move_layer", move_args, out, "moved-retrieved")
    other = await call(http, "inspect_scene", dict(object_id=neighbor["id"]))
    assert before["session_id"] == placed["session_id"] == moved["session_id"] == retrieved["session_id"]
    assert retrieved["revision"] == moved["revision"]
    assert other["objects"][0]["materials"] == neighbor["materials"]
    assert other["objects"][0]["layers"] == []
    p, m = placed["layers"], moved["layers"]
    assert len(p) == len(m) == 1 and p[0]["id"] == m[0]["id"] == "vector-logo"
    width = m[0]["width_mm"]
    delta = m[0]["offset_local_mm"]["x"] - p[0]["offset_local_mm"]["x"]
    assert abs(width - 40) < 1e-4 and abs(p[0]["width_mm"] - width) < 1e-4 and abs(delta - 3) < 1e-4

    def frame(result, index):
        return Image.open(out / result["image_metadata"][index]["file"]).convert("RGB")

    def distance(a, b):
        return ImageChops.difference(a, b).getbbox()

    a, b, c = (frame(result, 0) for result in (before, placed, moved))
    assert distance(a, frame(fast, 0)) is None
    assert distance(c, frame(retrieved, 0)) is None
    second = (360, 0, 700, 420)
    assert distance(a.crop(second), b.crop(second)) is None
    assert distance(a.crop(second), c.crop(second)) is None
    assert distance(frame(before, 1), frame(placed, 1)) is None
    assert distance(frame(before, 1), frame(moved, 1)) is None
    assert distance(frame(before, 2), frame(moved, 2)) is not None

    def orange_center(image):
        mask = [(x, y) for y in range(image.height) for x in range(350)
                if (rgb := image.getpixel((x, y)))[0] > 150 and rgb[0] - rgb[2] > 30]
        assert len(mask) > 500, len(mask)
        return sum(x for x, _ in mask) / len(mask)

    shift_pixels = orange_center(c) - orange_center(b)
    assert abs(shift_pixels * 310 / 700 - 3) < 0.35, shift_pixels
    verification = await verify_lids(stdio, http, out, before, moved, focused)
    summary = {"width_mm": width, "delta_local_x_mm": delta, "pixel_center_delta_x": shift_pixels,
               "pixel_center_delta_mm": shift_pixels * 310 / 700, "layer_count": len(m),
               "neighbor_material_preserved": True, "neighbor_changed_pixels": 0,
               "underside_changed_pixels": 0, "transports": ["stdio", "streamable-http"],
               "compact": compact, "full": full, "before": before, "before_fast": fast,
               "placed": placed, "moved": moved, "retrieved": retrieved, "focused": focused,
               "edit_verification": verification}
    contact_sheet(out)
    (out / "lids-result.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    print(json.dumps({key: value for key, value in summary.items() if not isinstance(value, dict)}), flush=True)
    print("Edit verification: %.2f ms with overlays, %.2f ms metrics-only, %.2f ms to render the same two views"
          % tuple(verification[key]["client_wall_ms"]
                  for key in ("verified", "numbers_only", "fresh_two_camera_render")), flush=True)
    return compact, moved


async def run(out, gui=False, extra=None, bool_addon=None, *, blender_executable="blender", scenario=run_lids):
    out.mkdir(parents=True, exist_ok=True)
    for name in ("blender.ready", "blender.stop"):
        (out / name).unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix="blender-mcp-profile-") as profile:
        env = dict(os.environ, BLENDER_MCP_TOKEN=secrets.token_urlsafe(32),
                   BLENDER_MCP_BRIDGE_PORT=str(port()), BLENDER_MCP_TEST_OUTPUT=str(out),
                   BLENDER_USER_RESOURCES=profile, BLENDER_MCP_TEST_EXECUTABLE=blender_executable)
        if bool_addon:
            target = Path(profile) / "scripts/addons/object_boolean_tools.py"
            target.parent.mkdir(parents=True)
            shutil.copyfile(bool_addon, target)
        if gui:
            archive = Path(profile) / "blender-bridge.zip"
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as package:
                for source in sorted((ROOT / "blender_bridge").glob("*.py")):
                    package.write(source, f"blender_bridge/{source.name}")
            env["BLENDER_MCP_TEST_ADDON_ZIP"] = str(archive)
            subprocess.run([blender_executable, "-b", "--factory-startup", "--python-exit-code", "1", "--python-expr",
                           "import bpy; bpy.context.preferences.view.show_splash = False; bpy.ops.wm.save_userpref()"],
                           env=env, stdout=subprocess.DEVNULL, check=True, timeout=20)
        http_port = port()
        with (out / "blender.log").open("wb") as blend_log, (out / "http.log").open("wb") as http_log:
            blender = subprocess.Popen([blender_executable, *([] if gui else ["-b"]), "-t", "4",
                                        *([] if gui else ["--factory-startup"]), "--python-exit-code", "1",
                                        "--python", str(ROOT / "tests/blender_fixture.py")],
                                       env=env, stdout=blend_log, stderr=subprocess.STDOUT)
            http_server = subprocess.Popen([sys.executable, "-m", "blender_mcp.server",
                                            "--transport", "streamable-http", "--port", str(http_port)],
                                           env=env, stdout=http_log, stderr=subprocess.STDOUT, cwd=ROOT)
            try:
                await readiness(blender, out / "blender.ready")
                async with session("stdio", env) as stdio, session("http", env, f"http://127.0.0.1:{http_port}/mcp") as http:
                    compact, moved = await scenario(stdio, http, out)
                    if gui:
                        await manual(out, "viewport-solid")
                        capture = await call(http, "observe", {"view": {"mode": "viewport"}}, out, "live")
                        image_path = out / capture["image_metadata"][0]["file"]
                        with Image.open(image_path) as image:
                            extrema = image.convert("RGB").getextrema()
                        assert any(high - low > 40 for low, high in extrema), "Viewport PNG is blank"
                        capture["rgb_extrema"] = extrema
                        assert capture["source"] == "viewport" and capture["session_id"] == compact["session_id"]
                        (out / "viewport-result.json").write_text(json.dumps(capture, indent=2))
                    if extra:
                        await extra(stdio, http, out, env, compact, moved)
            finally:
                (out / "blender.stop").touch()
                http_server.terminate()
                await asyncio.to_thread(http_server.wait, 10)
                if gui:
                    blender.terminate()
                try:
                    await asyncio.to_thread(blender.wait, 10)
                except subprocess.TimeoutExpired:
                    blender.kill()
                    await asyncio.to_thread(blender.wait)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=ROOT / "work/smoke")
    parser.add_argument("--gui", action="store_true")
    parser.add_argument("--blender", default="blender", help="Path to a Blender executable")
    args = parser.parse_args()
    asyncio.run(run(args.out.resolve(), args.gui, blender_executable=args.blender))


if __name__ == "__main__":
    main()
