"""Acceptance checks across real Blender, stdio, HTTP, and optional Codex CLI."""
import argparse
import asyncio
import json
from pathlib import Path

from PIL import Image, ImageDraw

from claude_client import check_claude
from codex_client import check_codex
from smoke import ROOT, call, manual, run


def make_mask(path, x1, x2):
    image = Image.new("RGBA", (300, 180))
    ImageDraw.Draw(image).rectangle((x1, 50, x2, 129), fill=(255, 255, 255, 255))
    image.save(path)


async def inspect(client, name=None):
    result = await call(client, "inspect_scene", {"full": name is not None})
    obj = next((obj for obj in result["objects"] if obj["name"] == name), None) if name else None
    return result, obj


async def fault(client, tool, args, phrase):
    result = await call(client, tool, args, allow_error=True)
    assert result.get("state") == "failed" and phrase in result["error"], result
    return result


async def reopen_checkpoint(path, out, env):
    code = f"""import bpy
from pathlib import Path
image = bpy.data.images["Checkpoint external texture"]
assert image.packed_file is None
assert Path(bpy.path.abspath(image.filepath)).resolve() == Path({str(out / "logo.png")!r})
image.reload()
assert tuple(image.size) == (480, 180)
assert len([node for node in bpy.data.objects["Lid A (selected)"].active_material.node_tree.nodes
            if "blender_mcp_layer" in node]) == 1
print("Relative external image loaded from the reopened checkpoint", flush=True)
"""
    transcript = out / "checkpoint-reopen.log"
    with transcript.open("wb") as log:
        process = await asyncio.create_subprocess_exec(env["BLENDER_MCP_TEST_EXECUTABLE"], "-b", "-t", "2",
                "--factory-startup", str(path), "--python-exit-code", "1", "--python-expr", code,
                env=env, stdout=log, stderr=log)
        try:
            exit_code = await asyncio.wait_for(process.wait(), 30)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise
    assert exit_code == 0, transcript.read_text(errors="replace")
    return {"relative_external_image": "loaded", "editable_layer": "preserved", "transcript": transcript.name}


async def acceptance(stdio, http, out, env, compact, moved, codex=False, claude=None):
    results = {"mode": "background" if compact["background"] else "gui", "session_id": compact["session_id"]}
    results["discovery"] = await call(stdio, "list_addons", {"refresh": True})
    addons = {item["adapter"]: item for item in results["discovery"]["addons"] if item["adapter"]}
    target_id = compact["active_object_id"]

    await manual(out, "checkpoint-relative-texture")
    checkpoint = out / "checkpoints" / "two-lids-after.blend"
    checkpoint.parent.mkdir(exist_ok=True)
    checkpoint.unlink(missing_ok=True)
    results["checkpoint"] = await call(http, "checkpoint", dict(scene_token=moved["scene_token"],
                                              path=str(checkpoint), job_id="save-copy"))
    with checkpoint.open("rb") as file:
        assert b"BLEND" in file.read(12)
    assert results["checkpoint"]["file"] == moved["file"]
    await manual(out, "check-original-texture-path")
    results["checkpoint_reopened"] = await reopen_checkpoint(checkpoint, out, env)

    decal_glb = await call(http, "verify_glb", dict(scene_token=moved["scene_token"], collection="Lids",
                      path=str(out / "projected-decal.glb"), job_id="glb-layer-bake", overwrite=True))
    assert decal_glb["asset_status"] == "needs_edit"
    assert any("editable Blender MCP image layers" in item["effects"] for item in decal_glb["needs_bake"])
    results["glb_projected_decal"] = decal_glb

    # Disconnect a client while an edit and its post-edit render are pending.
    from smoke import session

    pending_args = dict(job_id="survive-disconnect", wait=False, object_id=target_id,
        revision_token=moved["revision_token"], layer_id="vector-logo", delta_x_mm=1,
        view={"cameras": ["Top", "Close-up"]})
    async with session("stdio", env) as disposable:
        pending = await call(disposable, "move_layer", pending_args, allow_error=True)
        assert pending["state"] in {"queued", "running"}, pending
    results["after_disconnect"] = await call(http, "job_status", {"job_id": pending_args["job_id"]}, allow_error=True)
    queued = await call(http, "observe", dict(job_id="cancel-me", wait=False, view={"cameras": ["Top"]}), allow_error=True)
    assert queued["state"] == "queued", queued
    results["cancelled"] = await call(stdio, "cancel_job", {"job_id": "cancel-me"}, allow_error=True)
    assert results["cancelled"]["state"] == "cancelled"
    resumed = await call(http, "move_layer", {**pending_args, "wait": True}, out, "reconnected")
    results["reconnected"] = resumed
    assert resumed["session_id"] == compact["session_id"]
    assert len(resumed["image_metadata"]) == 2
    assert resumed["layers"][0]["offset_local_mm"]["x"] == 4
    inspected = await call(http, "inspect_scene")
    assert inspected["revision"] == resumed["revision"] and len(inspected["objects"][0]["layers"]) == 1
    reverted = await call(http, "rollback", dict(object_id=target_id, revision_token=resumed["revision_token"],
                                                steps=1, job_id="undo-reconnected"))
    moved.update(revision_token=reverted["revision_token"], revision=reverted["revision"])

    # A bad capture after a valid move must restore the prior material binding.
    await fault(http, "move_layer", dict(object_id=target_id, job_id="bad-capture",
                revision_token=moved["revision_token"], layer_id="vector-logo",
                delta_x_mm=12, view={"cameras": ["No such camera"]}), "must name a camera")
    unchanged = await call(stdio, "inspect_scene")
    assert unchanged["revision"] == moved["revision"]
    assert unchanged["objects"][0]["revision_token"] == moved["revision_token"]
    await manual(out, "change-layer-texture")
    try:
        results["stale_nested_shader"] = await fault(http, "move_layer", {
            "object_id": target_id, "revision_token": moved["revision_token"], "job_id": "stale-layer-graph",
            "layer_id": "vector-logo", "delta_x_mm": 1,
        }, "Stale revision")
    finally:
        await manual(out, "restore-layer-texture")
    updated = await call(stdio, "update_layer", dict(object_id=target_id, job_id="refine-finish",
                       revision_token=moved["revision_token"], layer_id="vector-logo", opacity=0.7, roughness=0.6))
    assert len(updated["layers"]) == 1 and abs(updated["layers"][0]["roughness"] - 0.6) < 1e-5
    results["finish_update"] = updated
    refined = await call(http, "rollback", dict(object_id=target_id, revision_token=updated["revision_token"],
                                              steps=1, job_id="undo-finish"))
    await manual(out, "neighbor")
    results["stale_selection"] = await fault(stdio, "move_layer",
        dict(object_id=target_id, job_id="stale-target", revision_token=refined["revision_token"],
             layer_id="vector-logo", delta_x_mm=7), "Stale")
    await manual(out, "lid")
    token = (await call(http, "inspect_scene"))["objects"][0]["revision_token"]
    results["rollback"] = await call(http, "rollback", dict(object_id=target_id, revision_token=token,
                                                            steps=2, job_id="rollback-two"))
    assert len(results["rollback"]["undone"]) == 2
    scene = await call(stdio, "inspect_scene")
    assert scene["objects"][0]["layers"] == []
    assert scene["objects"][0]["materials"] == compact["objects"][0]["materials"]

    svg = out / "editable.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="48" height="24"><path fill="#FA0" d="M1 1H47V23H1Z"/></svg>')
    results["svg"] = await call(stdio, "import_svg", dict(scene_token=scene["scene_token"],
                                                        path=str(svg), job_id="svg-import"))
    assert len(results["svg"]["objects"]) == 1 and results["svg"]["objects"][0]["type"] == "CURVE"
    scene, obj = await inspect(stdio, "Lid A (selected)")
    results["svg_undo"] = await call(stdio, "rollback", dict(object_id=obj["id"],
                            revision_token=obj["revision_token"], job_id="svg-undo"))

    await manual(out, "build")
    await manual(out, "only-body")
    if addons["bool_tool"]["state"] == "missing":
        results["bool_tool"] = {"state": "missing"}
    else:
        module = addons["bool_tool"]["module"]
        assert addons["bool_tool"]["state"] == "installed"
        results["bool_activation"] = await call(stdio, "enable_addon", dict(module=module, job_id="bool-enable"))
        assert results["bool_activation"]["addon"]["state"] == "active"
        scene, body = await inspect(stdio, "Case")
        _, cutter = await inspect(stdio, "Case cutter")
        args = dict(object_id=body["id"], revision_token=body["revision_token"], cutter_id=cutter["id"],
                    cutter_token=cutter["revision_token"], stack_index=0)
        results["bool_bad_selection"] = await fault(stdio, "bool_difference",
                                                    {**args, "job_id": "bool-bad"}, "Select exactly")
        await manual(out, "body-and-cutter")
        scene, body = await inspect(http, "Case")
        _, cutter = await inspect(http, "Case cutter")
        args.update(revision_token=body["revision_token"], cutter_token=cutter["revision_token"])
        results["bool_tool"] = await call(http, "bool_difference",
                     {**args, "job_id": "bool-good", "view": {"cameras": ["Case front", "Case back"]}}, out, "boolean")
        modifier = results["bool_tool"]["modifier"]
        assert modifier["stack_index"] == 0 and modifier["operation"] == "DIFFERENCE"
        assert modifier["evaluated_faces"] > modifier["base_faces"]
        await manual(out, "only-body")
        scene, body = await inspect(stdio, "Case")
        results["bool_undo"] = await call(stdio, "rollback", dict(object_id=body["id"],
                                   revision_token=body["revision_token"], job_id="bool-undo"))

    if addons["node_wrangler"]["state"] != "missing":
        module = addons["node_wrangler"]["module"]
        results["node_activation"] = await call(http, "enable_addon", dict(module=module, job_id="nw-enable"))
        scene, body = await inspect(stdio, "Case")
        args = dict(object_id=body["id"], revision_token=body["revision_token"], node_name="Principled BSDF")
        if compact["background"]:
            results["node_wrong_editor"] = await fault(stdio, "node_texture",
                                      {**args, "job_id": "nw-background"}, "GUI Shader Editor")
        else:
            results["node_wrong_editor"] = await fault(http, "node_texture",
                                     {**args, "job_id": "nw-bad", "area_type": "viewport"}, "poll()")
            await manual(out, "shader-editor")
            results["node_wrangler"] = await call(stdio, "node_texture", {**args, "job_id": "nw-good"})
            assert {node["type"] for node in results["node_wrangler"]["nodes"]} == {"MAPPING", "TEX_COORD", "TEX_IMAGE"}
            _, body = await inspect(stdio, "Case")
            results["node_undo"] = await call(stdio, "rollback", dict(object_id=body["id"],
                                     revision_token=body["revision_token"], job_id="nw-undo"))

    front, side = out / "front-mask.png", out / "side-mask.png"
    make_mask(front, 75, 224)
    make_mask(side, 105, 194)
    views = [
        {"camera": "Case front", "role": "front", "mask_path": str(front), "landmarks": [
            {"object_name": "Case screw", "center": [0.35, 0.40]},
            {"object_name": "Case port", "center": [0.60, 0.50]}]},
        {"camera": "Case side", "role": "side", "mask_path": str(side)},
    ]
    spec = dict(subjects=["Case", "Case screw", "Case port"], views=views, depth_mm=60)
    scene = await call(http, "inspect_scene")
    results["reference_spec"] = await call(http, "set_reference_spec", dict(scene_token=scene["scene_token"],
                                               spec_id="case", job_id="ref-set", spec=spec))
    for defect, expected in (("missing-screw", "missing"), ("shifted-port", "shifted"), ("wrong-depth", None),
                             ("correct", None)):
        await manual(out, defect)
        report = await call(http, "compare_reference", {"spec_id": "case"}, out, defect)
        results[defect] = report
        assert report["asset_status"] == ("ready" if defect == "correct" else "needs_edit"), report
        if expected:
            assert expected in [mark["status"] for mark in report["views"][0]["landmarks"]], report
        elif defect == "wrong-depth":
            assert report["depth"]["status"] == "out_of_tolerance", report
    results["final"] = await call(stdio, "finalize_asset", {"spec_id": "case", "job_id": "finalize-case"})
    assert results["final"]["asset_status"] == "ready"
    scene = await call(http, "inspect_scene")
    await call(http, "set_reference_spec", dict(scene_token=scene["scene_token"],
              spec_id="single-view", job_id="ref-front-only", spec={**spec, "views": views[:1]}))
    results["single_view"] = await call(http, "compare_reference", {"spec_id": "single-view"})
    assert results["single_view"]["asset_status"] == "insufficient_data"
    assert results["single_view"]["depth"]["status"] == "unknown"

    scene = await call(stdio, "inspect_scene")
    results["glb"] = await call(stdio, "verify_glb", dict(scene_token=scene["scene_token"],
             collection="GLB fixture", job_id="glb-export", path=str(out / "roundtrip.glb"), overwrite=True))
    assert results["glb"]["asset_status"] == "ready", results["glb"]
    assert (out / "roundtrip.glb").read_bytes()[:4] == b"glTF"
    assert any(mat["channels"].get("Base Color", {}).get("image_size") == [480, 180]
               for item in results["glb"]["imported"].values() for mat in item["materials"]
               if isinstance(mat["channels"].get("Base Color"), dict))
    if codex:
        results["codex_cli"] = await check_codex(out, env, compact["session_id"])
    if claude:
        results["claude_code"] = await check_claude(out, env, claude)
    (out / "acceptance-result.json").write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print("Acceptance checks passed in", results["mode"], flush=True)
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=ROOT / "work/acceptance")
    parser.add_argument("--gui", action="store_true")
    parser.add_argument("--blender", default="blender", help="Path to a Blender executable")
    parser.add_argument("--bool-addon", type=Path)
    parser.add_argument("--codex", action="store_true")
    parser.add_argument("--claude", nargs="?", const="claude", help="Run Claude Code, optionally by explicit path")
    parser.add_argument("--require-agents", action="store_true",
                        help="Run both clients and require successful model-backed MCP calls")
    args = parser.parse_args()
    report = {}

    async def extra(*payload):
        report.update(await acceptance(*payload, codex=args.codex or args.require_agents,
                                       claude=args.claude or ("claude" if args.require_agents else None)))

    asyncio.run(run(args.out.resolve(), args.gui, extra, args.bool_addon, blender_executable=args.blender))
    if args.require_agents:
        pending = [name for name in ("codex_cli", "claude_code")
                   if report.get(name, {}).get("agent_turn", {}).get("status") != "passed"]
        if pending:
            raise SystemExit(f"Model-backed acceptance pending for {', '.join(pending)}. "
                             f"See {args.out.resolve() / 'acceptance-result.json'}.")


if __name__ == "__main__":
    main()
