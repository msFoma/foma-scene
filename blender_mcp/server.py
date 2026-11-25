"""One tool catalog and client for stdio and local Streamable HTTP."""
import argparse
import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ImageContent, TextContent
from pydantic import BaseModel, Field, model_validator

from . import edit_verification

logging.getLogger("httpx").setLevel(logging.WARNING)

server = FastMCP("Blender MCP", host="127.0.0.1", json_response=True,
                 instructions="Inspect the active scene first. Use its revision token and a unique "
                              "job ID for each edit. Confirm the result with observe and measurements.",
                 transport_security=TransportSecuritySettings(
                     enable_dns_rebinding_protection=True,
                     allowed_hosts=["127.0.0.1:*", "localhost:*"],
                     allowed_origins=["http://127.0.0.1:*", "http://localhost:*"]))


class View(BaseModel):
    mode: Literal["camera", "viewport"] = "camera"
    cameras: Annotated[list[str], Field(max_length=6)] = Field(default_factory=list)
    resolution_percent: Annotated[int, Field(ge=10, le=100)] | None = None
    fast: bool = True


Content = list[TextContent | ImageContent]


def bridge_url(path):
    port = int(os.getenv("BLENDER_MCP_BRIDGE_PORT", "8765"))
    return f"http://127.0.0.1:{port}/{path}"


def bridge_client():
    token = os.getenv("BLENDER_MCP_TOKEN", "")
    return httpx.AsyncClient(timeout=5, headers={"Authorization": f"Bearer {token}"}, trust_env=False)


def bridge_error(exc):
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.json().get("error", str(exc))
    return "Blender bridge unavailable. Start it in the open session and check the port."


def result_content(result) -> Content:
    images = result.pop("images", [])
    metadata = [{key: value for key, value in image.items() if key != "data"} for image in images]
    if metadata:
        result["image_metadata"] = metadata
    content = [TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]
    content.extend(ImageContent(type="image", data=image["data"], mimeType=image["mime_type"])
                   for image in images)
    return content


async def invoke(action, args, job_id=None, wait=True, **flags) -> Content:
    job_id = job_id or uuid4().hex
    url = bridge_url("job")
    started = time.perf_counter()
    try:
        async with bridge_client() as client:
            payload = {"job_id": job_id, "action": action, "args": args, "await_result": wait, **flags}
            while True:
                tick = time.perf_counter()
                response = await client.post(url, json=payload)
                response.raise_for_status()
                result = response.json()
                if not wait or result["state"] not in ("queued", "running") or time.perf_counter() - started > 25:
                    break
            final_poll_ms = round((time.perf_counter() - tick) * 1000, 2)
    except httpx.HTTPError as exc:
        return result_content({"job_id": job_id, "error": bridge_error(exc)})
    result["mcp_timing_ms"] = {
        "final_bridge_request": final_poll_ms,
        "server_wall": round((time.perf_counter() - started) * 1000, 2),
    }
    return result_content(result)


@server.tool(structured_output=False)
async def inspect_scene(object_id: int | None = None, full: bool = False, collection: str | None = None,
                        offset: int = 0, limit: int = 30, detail: Literal["edit", "summary"] = "edit") -> Content:
    """Read file, session, selection and cameras. Use summary to browse large scenes; use edit
    detail for layers and current write guards for the object you intend to change."""
    return await invoke("inspect", dict(object_id=object_id, full=full, collection=collection,
                                        offset=offset, limit=limit, detail=detail))


@server.tool(structured_output=False)
async def observe(object_id: int | None = None, view: View | None = None,
                  job_id: str | None = None, wait: bool = True) -> Content:
    """Return actual image pixels. Use viewport for an open editor or camera with explicit viewpoints."""
    return await invoke("observe", dict(object_id=object_id, **(view or View()).model_dump()), job_id, wait)


Fraction = Annotated[float, Field(ge=0, le=1)]
PixelCount = Annotated[int, Field(ge=0)]


class RegionCheck(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=100)]
    box: tuple[Fraction, Fraction, Fraction, Fraction] = (0, 0, 1, 1)
    min_changed_pixels: PixelCount | None = None
    max_changed_pixels: PixelCount | None = None

    @model_validator(mode="after")
    def check_limits(self):
        x0, y0, x1, y1 = self.box
        minimum, maximum = self.min_changed_pixels, self.max_changed_pixels
        if x0 >= x1 or y0 >= y1:
            raise ValueError("Region box must have positive width and height")
        if minimum is None and maximum is None:
            raise ValueError("Set a minimum, maximum, or both changed-pixel counts")
        if minimum is not None and maximum is not None and minimum > maximum:
            raise ValueError("The minimum changed-pixel count must not exceed the maximum")
        return self


class CameraCheck(BaseModel):
    camera: str
    regions: Annotated[list[RegionCheck], Field(min_length=1, max_length=32)]


@server.tool(structured_output=False)
async def verify_edit(before_job_id: str, after_job_id: str,
                      checks: Annotated[list[CameraCheck], Field(min_length=1, max_length=6)],
                      pixel_threshold: Annotated[int, Field(ge=0, le=254)] = 0,
                      include_overlays: bool = True) -> Content:
    """Check visible changes between retained camera renders without rendering again. Use observe or
    edit jobs with view images from the same scene and frame. Region boxes are normalized top-left
    [x0,y0,x1,y1]. Use max_changed_pixels=0 to protect a region and min_changed_pixels>=1 for the
    intended edit. A pixel changes when a premultiplied RGBA channel exceeds pixel_threshold."""
    cameras = [check.camera for check in checks]
    if len(set(cameras)) != len(cameras):
        return result_content({"state": "failed", "error": "Specify each camera once"})
    started = time.perf_counter()
    try:
        async with bridge_client() as client:
            response = await client.post(bridge_url("frames"), json={
                "before_job_id": before_job_id, "after_job_id": after_job_id, "cameras": cameras})
            response.raise_for_status()
            pair = response.json()
        fetch_ms = round((time.perf_counter() - started) * 1000, 2)
        result = await asyncio.to_thread(edit_verification.compare, pair, checks, pixel_threshold, include_overlays)
    except httpx.HTTPError as exc:
        return result_content({"state": "failed", "error": bridge_error(exc)})
    except (OSError, ValueError) as exc:
        return result_content({"state": "failed", "error": str(exc)})
    result["timing_ms"]["fetch_retained_frames"] = fetch_ms
    result["mcp_timing_ms"] = {"server_wall": round((time.perf_counter() - started) * 1000, 2)}
    return result_content(result)


@server.tool(structured_output=False)
async def apply_layer(object_id: int, revision_token: str, job_id: str, layer_id: str, image_path: str,
                      width_mm: float, depth_mm: tuple[float, float], offset_u_mm: float = 0,
                      offset_v_mm: float = 0, projection: Literal["+X", "+Y", "+Z", "-X", "-Y", "-Z"] = "+Z",
                      channels: tuple[Literal["color", "roughness"], ...] = ("color",),
                      opacity: float = 1, roughness: float = 0.8, slot: int = 0, view: View | None = None,
                      wait: bool = True) -> Content:
    """Add an editable, alpha-masked image layer to the active mesh material slot. Signed depth in mm
    clips the projection. For +Z, U and V are local X and Y. A supplied view returns post-edit images."""
    args = dict(object_id=object_id, revision_token=revision_token, layer_id=layer_id,
                image_path=image_path, width_mm=width_mm, depth_mm=depth_mm, offset_u_mm=offset_u_mm,
                offset_v_mm=offset_v_mm, projection=projection, channels=channels, opacity=opacity,
                roughness=roughness, slot=slot, view=view.model_dump() if view else None)
    return await invoke("apply_layer", args, job_id, wait)


@server.tool(structured_output=False)
async def move_layer(object_id: int, revision_token: str, job_id: str, layer_id: str, delta_x_mm: float,
                     slot: int = 0, view: View | None = None, wait: bool = True) -> Content:
    """Move the existing layer by a signed physical distance along the active object's local X."""
    return await invoke("move_layer", dict(object_id=object_id, revision_token=revision_token,
                                           layer_id=layer_id, delta_x_mm=delta_x_mm, slot=slot,
                                           view=view.model_dump() if view is not None else None), job_id, wait)


@server.tool(structured_output=False)
async def update_layer(object_id: int, revision_token: str, job_id: str, layer_id: str,
                       opacity: float | None = None, roughness: float | None = None,
                       slot: int = 0, view: View | None = None) -> Content:
    """Adjust opacity or finish of an existing masked layer without adding another layer."""
    if opacity is None and roughness is None:
        raise ValueError("Supply opacity or roughness")
    return await invoke("update_layer", dict(object_id=object_id, revision_token=revision_token,
        layer_id=layer_id, opacity=opacity, roughness=roughness, slot=slot,
        view=view.model_dump() if view is not None else None), job_id)


@server.tool(structured_output=False)
async def job_status(job_id: str) -> Content:
    """Check a previously submitted job after a timeout or reconnect. Reuse identical arguments to retrieve its result."""
    return await invoke(None, {}, job_id, False, status_only=True)


@server.tool(structured_output=False)
async def cancel_job(job_id: str) -> Content:
    """Cancel a queued job. A running Blender operator reports running until it finishes."""
    return await invoke(None, {}, job_id, False, cancel=True)


@server.tool(structured_output=False)
async def rollback(object_id: int, revision_token: str, job_id: str, steps: int = 1) -> Content:
    """Roll back recent MCP edits in reverse order while their write guards still match."""
    return await invoke("rollback", dict(object_id=object_id, revision_token=revision_token, steps=steps), job_id)


@server.tool(structured_output=False)
async def list_addons(refresh: bool = False) -> Content:
    """List installed and active add-ons, registered supported operators, and missing adapters."""
    return await invoke("list_addons", {"refresh": refresh})


@server.tool(structured_output=False)
async def enable_addon(module: str, job_id: str) -> Content:
    """Activate an already installed add-on in this Blender process. Use its exact discovered module."""
    return await invoke("enable_addon", {"module": module}, job_id)


@server.tool(structured_output=False)
async def import_svg(scene_token: str, job_id: str, path: str) -> Content:
    """Run SVG Import against a local SVG and return IDs of the added editable curves."""
    return await invoke("import_svg", {"scene_token": scene_token, "path": path}, job_id)


GearTeeth = Annotated[int, Field(ge=18, le=96)]


@server.tool(structured_output=False)
async def create_gear_train(
    scene_token: str, job_id: str,
    teeth: Annotated[tuple[GearTeeth, ...], Field(min_length=2, max_length=4)] = (18, 30, 54),
    module_mm: Annotated[float, Field(ge=1, le=10)] = 3,
    thickness_mm: Annotated[float, Field(ge=3, le=30)] = 12,
    backlash_mm: Annotated[float, Field(gt=0, le=5)] = 0.4,
    revolution_frames: Annotated[int, Field(ge=24, le=360)] = 72,
    origin_mm: tuple[float, float, float] = (0, 0, 0),
    studio: bool = True,
) -> Content:
    """Create animated involute spur gears in a new collection. Specify teeth from input to output.
    Dimensions use millimeters. Frame 1 starts the animation. The studio option adds lights and
    named hero and plan cameras. Reports gear ratio, pitch spacing, and sampled tooth clearance."""
    return await invoke("create_gear_train", dict(scene_token=scene_token, teeth=teeth, module_mm=module_mm,
        thickness_mm=thickness_mm, backlash_mm=backlash_mm, revolution_frames=revolution_frames,
        origin_mm=origin_mm, studio=studio), job_id)


@server.tool(structured_output=False)
async def bool_difference(object_id: int, revision_token: str, cutter_id: int, cutter_token: str,
                          job_id: str, stack_index: int | None = None, view: View | None = None) -> Content:
    """Use the installed Bool Tool brush operator on the active body and selected mesh cutter."""
    return await invoke("bool_difference", dict(object_id=object_id, revision_token=revision_token,
        cutter_id=cutter_id, cutter_token=cutter_token, stack_index=stack_index,
        view=view.model_dump() if view else None), job_id)


@server.tool(structured_output=False)
async def node_texture(object_id: int, revision_token: str, node_name: str, job_id: str, slot: int = 0,
                       area_type: Literal["shader_editor", "viewport"] = "shader_editor") -> Content:
    """Run Node Wrangler Texture Setup on a named node in an open Shader Editor."""
    return await invoke("node_texture", dict(object_id=object_id, revision_token=revision_token,
        node_name=node_name, slot=slot, area_type=area_type), job_id)


class Landmark(BaseModel):
    object_name: str
    center: tuple[Fraction, Fraction]
    max_error: Fraction = 0.01


class ReferenceView(BaseModel):
    camera: str
    role: Literal["front", "side", "other"]
    mask_path: str
    minimum_iou: Fraction = 0.95
    landmarks: Annotated[list[Landmark], Field(max_length=100)] = Field(default_factory=list)


class ReferenceSpec(BaseModel):
    subjects: Annotated[list[str], Field(min_length=1, max_length=200)]
    views: Annotated[list[ReferenceView], Field(min_length=1, max_length=6)]
    depth_mm: Annotated[float, Field(gt=0)] | None = None
    depth_tolerance_mm: Annotated[float, Field(ge=0)] = 1
    require_depth: bool = True


@server.tool(structured_output=False)
async def set_reference_spec(scene_token: str, job_id: str, spec_id: str, spec: ReferenceSpec) -> Content:
    """Store orthographic alpha masks and normalized landmark targets in the current scene session."""
    return await invoke("set_reference", dict(scene_token=scene_token, spec_id=spec_id, spec=spec.model_dump()), job_id)


@server.tool(structured_output=False)
async def compare_reference(spec_id: str, job_id: str | None = None, wait: bool = True) -> Content:
    """Compare current evaluated geometry to registered orthographic masks and landmarks; return overlay images."""
    return await invoke("compare_reference", {"spec_id": spec_id}, job_id, wait)


@server.tool(structured_output=False)
async def finalize_asset(spec_id: str, job_id: str) -> Content:
    """Re-run the registered reference checks and derive readiness from the current geometry."""
    return await invoke("compare_reference", {"spec_id": spec_id}, job_id)


@server.tool(structured_output=False)
async def verify_glb(scene_token: str, job_id: str, collection: str, path: str,
                     overwrite: bool = False, wait: bool = True) -> Content:
    """Export a collection to GLB and reimport in a clean Blender scene. Report differences and baking needs."""
    return await invoke("export_glb", dict(scene_token=scene_token, collection=collection, path=path,
        overwrite=overwrite), job_id, wait)


@server.tool(structured_output=False)
async def checkpoint(scene_token: str, job_id: str, path: str) -> Content:
    """Save a copy of the current Blender scene to a new .blend path."""
    return await invoke("checkpoint", dict(scene_token=scene_token, path=path), job_id)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--transport", choices=("stdio", "streamable-http"), default="stdio")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--bridge-config", type=Path,
                        help="Local credentials written by the Foma Scene installer")
    args = parser.parse_args()
    if args.bridge_config:
        try:
            config = json.loads(args.bridge_config.read_text(encoding="utf-8"))
            token, port = config["token"], config["port"]
            if not isinstance(token, str) or len(token) < 16 or type(port) is not int or not 1024 <= port <= 65535:
                raise ValueError
        except (OSError, ValueError, KeyError, TypeError):
            parser.error("Cannot read bridge credentials. Run scripts/setup_scene.py again.")
        os.environ.setdefault("BLENDER_MCP_TOKEN", token)
        os.environ.setdefault("BLENDER_MCP_BRIDGE_PORT", str(port))
    server.settings.port = args.port
    server.run(transport=args.transport)


if __name__ == "__main__":
    main()
