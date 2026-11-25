"""Measure the normal MCP inspection and stale-write guard on a large Blender scene."""
import argparse
import asyncio
import json
import statistics
from pathlib import Path

from smoke import ROOT, call, logo, manual, run


async def measure(client, args, repetitions):
    results = [await call(client, "inspect_scene", args) for _ in range(repetitions)]
    return results[-1], {
        "samples": repetitions,
        "wall_median_ms": round(statistics.median(item["client_wall_ms"] for item in results), 2),
        "blender_median_ms": round(statistics.median(item["timing_ms"]["blender_total"] for item in results), 2),
        "response_json_bytes": len(json.dumps(results[-1], ensure_ascii=False).encode()),
    }


async def scenario(stdio, http, out):
    await manual(out, "build-large")
    dense, details = await measure(http, {"collection": "Dense instances", "limit": 200}, 5)
    assert dense["total"] == len(dense["objects"]) == 200
    assert len({item["revision_token"] for item in dense["objects"]}) == 200
    selected = next(item for item in dense["objects"] if item["id"] == dense["active_object_id"])
    one, active = await measure(http, {"object_id": selected["id"]}, 5)
    assert one["objects"][0]["revision_token"] == selected["revision_token"]
    page = await call(stdio, "inspect_scene", {"collection": "Scale fixture", "offset": 4990, "limit": 20})
    assert page["total"] == 5000 and len(page["objects"]) == 10
    tools = {item.name: item for item in (await http.list_tools()).tools}
    report = {"blender": dense["blender_version"], "objects": 5000, "dense_instances": 200,
              "quad_faces_per_dense_instance": 25600, "detail_page": details, "active": active}
    if "detail" in tools["inspect_scene"].inputSchema["properties"]:
        summary, timing = await measure(http, {"collection": "Dense instances", "limit": 200, "detail": "summary"}, 5)
        assert [item["id"] for item in summary["objects"]] == [item["id"] for item in dense["objects"]]
        assert all("revision_token" not in item and "layers" not in item for item in summary["objects"])
        report["summary_page"] = timing

    await manual(out, "change-shared-mesh")
    stale = await call(stdio, "move_layer", {"object_id": selected["id"], "revision_token": selected["revision_token"],
                       "job_id": "shared-edit-must-invalidate", "layer_id": "missing", "delta_x_mm": 1},
                       allow_error=True)
    assert stale["state"] == "failed" and "Stale revision" in stale["error"], stale
    after = await call(http, "inspect_scene", {"collection": "Dense instances", "limit": 200})
    assert all(before["revision_token"] != new["revision_token"]
               for before, new in zip(dense["objects"], after["objects"], strict=True))
    report["shared_geometry_guard"] = "passed"
    target = next(item for item in after["objects"] if item["id"] == selected["id"])
    peer = next(item for item in after["objects"] if item["id"] != target["id"])
    art = out / "scale-logo.png"
    logo(art)
    edited = await call(stdio, "apply_layer", {"object_id": target["id"], "revision_token": target["revision_token"],
                        "layer_id": "large-logo", "job_id": "large-scene-decal", "image_path": str(art),
                        "width_mm": 40, "depth_mm": [-1, 5], "offset_u_mm": 80, "offset_v_mm": 80})
    neighbor = (await call(http, "inspect_scene", {"object_id": peer["id"]}))["objects"][0]
    assert neighbor["materials"] == peer["materials"] and neighbor["layers"] == []
    assert edited["layers"][0]["width_mm"] == 40
    rolled = await call(http, "rollback", {"object_id": target["id"],
                        "revision_token": edited["revision_token"], "job_id": "large-scene-rollback"})
    restored = (await call(stdio, "inspect_scene", {"object_id": target["id"]}))["objects"][0]
    assert rolled["undone"] and restored["materials"] == target["materials"] and restored["layers"] == []
    report["large_scene_edit"] = {"width_mm": 40, "operation_ms": edited["timing_ms"]["operation"],
                                  "shared_neighbor": "preserved", "rollback": "passed"}
    (out / "scalability-result.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report), flush=True)
    return dense, None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=ROOT / "work/scalability")
    parser.add_argument("--blender", default="blender", help="Path to a Blender executable")
    args = parser.parse_args()
    asyncio.run(run(args.out.resolve(), blender_executable=args.blender, scenario=scenario))


if __name__ == "__main__":
    main()
