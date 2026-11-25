"""Compare the visible pixels of retained Blender camera renders."""
import base64
import io
import time
from functools import reduce

from PIL import Image, ImageChops, ImageDraw, ImageOps

MAX_PIXELS = 16_000_000


def decode(packet):
    width, height = packet["dimensions"]
    if width * height > MAX_PIXELS:
        raise ValueError("Render smaller camera views for comparison (up to 16 million pixels per view)")
    with Image.open(io.BytesIO(base64.b64decode(packet["data"])), formats=["PNG"]) as image:
        return image.convert("RGBA")


def difference_mask(before, after, threshold):
    # Premultiplication ignores RGB hidden behind full transparency and still compares alpha.
    left, right = before.convert("RGBa").split(), after.convert("RGBa").split()
    maximum = reduce(ImageChops.lighter, (ImageChops.difference(a, b) for a, b in zip(left, right)))
    return maximum.point([0] * (threshold + 1) + [255] * (255 - threshold))


def region_result(mask, check):
    x0, y0, x1, y1 = check.box
    width, height = mask.size
    box = round(x0 * width), round(y0 * height), round(x1 * width), round(y1 * height)
    if box[0] >= box[2] or box[1] >= box[3]:
        raise ValueError(f"The region {check.name!r} is empty at {width} × {height}")
    cropped = mask.crop(box)
    count = cropped.histogram()[255]
    bounds = cropped.getbbox()
    return {
        "name": check.name, "box_px": box,
        "changed_pixels": count, "total_pixels": cropped.width * cropped.height,
        "changed_bounds_px": ([value + box[index % 2] for index, value in enumerate(bounds)]
                              if bounds else None),
        "min_changed_pixels": check.min_changed_pixels,
        "max_changed_pixels": check.max_changed_pixels,
        "passed": ((check.min_changed_pixels is None or count >= check.min_changed_pixels)
                   and (check.max_changed_pixels is None or count <= check.max_changed_pixels)),
    }


def overlay(camera, after, mask, regions):
    background = Image.new("RGBA", after.size, "#212633")
    visible = Image.alpha_composite(background, after).convert("RGB")
    grayscale = ImageOps.grayscale(visible).point(lambda value: 24 + value // 3)
    canvas = Image.composite(Image.new("RGB", after.size, "#ff4277"), grayscale.convert("RGB"), mask)
    draw = ImageDraw.Draw(canvas)
    for region in regions:
        x0, y0, x1, y1 = region["box_px"]
        draw.rectangle((x0, y0, x1 - 1, y1 - 1), outline="#70e7ad" if region["passed"] else "#ffc95c")
    png = io.BytesIO()
    canvas.save(png, format="PNG", compress_level=3)
    data = png.getvalue()
    return {"label": f"{camera} edit overlay", "mime_type": "image/png", "bytes": len(data),
            "dimensions": after.size, "data": base64.b64encode(data).decode("ascii")}


def compare(pair, checks, threshold, include_overlays):
    started = time.perf_counter()
    before, after = pair["before"], pair["after"]
    for key in ("session_id", "scene", "frame"):
        if before[key] != after[key]:
            raise ValueError("Use camera renders from the same Blender session, scene, and frame")

    reports, images = [], []
    input_bytes = 0
    for check, left, right in zip(checks, before["images"], after["images"], strict=True):
        signature = left.get("camera_signature")
        if (not signature or signature != right.get("camera_signature")
                or left["dimensions"] != right["dimensions"]):
            raise ValueError(f"{check.camera}: use matching camera projection and render settings")
        new = decode(right)
        mask = difference_mask(decode(left), new, threshold)
        regions = [region_result(mask, region) for region in check.regions]
        reports.append({
            "camera": check.camera, "dimensions": new.size, "changed_pixels": mask.histogram()[255],
            "changed_bounds_px": mask.getbbox(), "regions": regions,
            "passed": all(region["passed"] for region in regions),
        })
        input_bytes += left["bytes"] + right["bytes"]
        if include_overlays:
            images.append(overlay(check.camera, new, mask, regions))

    return {
        "state": "succeeded", "source": "retained_camera_renders", "new_renders": 0,
        "session_id": before["session_id"], "scene": before["scene"], "frame": before["frame"],
        "before": {"job_id": before["job_id"], "revision": before["revision"]},
        "after": {"job_id": after["job_id"], "revision": after["revision"]},
        "passed": all(report["passed"] for report in reports), "pixel_threshold": threshold,
        "input_png_bytes": input_bytes, "views": reports, "images": images,
        "overlay_legend": {"pink pixels": "changed", "green outline": "passed", "yellow outline": "failed"}
                          if include_overlays else None,
        "timing_ms": {"compare_and_pack": round((time.perf_counter() - started) * 1000, 2)},
    }
