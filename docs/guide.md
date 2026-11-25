# Foma Scene guide

[English overview](../README.md) · [Русский обзор](../README.ru.md)

Local MCP server and Blender add-on for editing an open scene. The Blender process owns the scene, session identifiers, job queue and rollback history. A network worker accepts authenticated loopback requests. The add-on executes every `bpy` operation on Blender's main thread. The external MCP process uses the same tool catalog for stdio and Streamable HTTP.

## Run the two-lid scenario

Use Blender 4.3+ in PATH and Python 3.11+. The fixture uses DejaVu Sans Bold when drawing the test logo.

```sh
python -m pip install -e .
python tests/smoke.py
```

The SDK client starts one real Blender, initializes separate stdio and Streamable HTTP sessions, places a 40 mm logo and moves the same layer by +3 mm along local X. It decodes the images from the MCP responses and compares the neighboring lid and underside pixel by pixel. Under `work/smoke/` it writes `before-after.png`, the source and result images, `lids-result.json` and the original `two-lids.blend` fixture.

## Connect an open Blender session

For the default stdio setup, run `python scripts/setup_scene.py --client codex` or use `--client claude`. The installer saves the add-on preferences in the selected Blender installation's user profile and pairs the local MCP server with the add-on. Close Blender before installing so it reloads those preferences at startup. Use Blender and the MCP client on the same machine. After setup, open Blender, press **N** in the 3D Viewport, and click **Blender MCP → Connect**.

### Manual setup and local HTTP

1. Package the add-on with `mkdir -p work && python -m zipfile -c work/blender-bridge.zip blender_bridge`. Install it under **Edit → Preferences → Add-ons → Install from Disk** and enable **Foma Scene · Blender MCP**. Set the bridge token in the add-on preferences. Then open **Blender MCP** in the 3D Viewport sidebar and click **Connect**.
2. Use a local secret of at least 16 characters. Set the same value as `BLENDER_MCP_TOKEN` for the MCP process. If launching Blender from a shell, the environment variable overrides the token saved in Add-on Preferences. For a shell-launched Blender and client, generate it in their parent shell with `export BLENDER_MCP_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"`. The bridge port defaults to 8765. To change it, set Add-on Preferences and `BLENDER_MCP_BRIDGE_PORT` for the MCP process. For scripted sessions the environment variable overrides the Blender preference too.
3. For stdio, start the client through the same environment. The command is `python -m blender_mcp.server`. For local Streamable HTTP, start `python -m blender_mcp.server --transport streamable-http --port 8766`. Clients attach to `http://127.0.0.1:8766/mcp`. The HTTP endpoint is for trusted local clients. The token authenticates the server-to-Blender bridge. Use the environment that can reach Blender's loopback interface.

### Claude Code and Codex

Both client paths point at the canonical `skills/blender-mcp/SKILL.md`. For stdio, the following commands configure the connection. Run them where the Python package is installed and the shared bridge environment variables are set.

```sh
claude mcp add --transport stdio blender -- python -m blender_mcp.server
codex mcp add blender -- python -m blender_mcp.server
```

For HTTP, run the external server first, then register it.

```sh
claude mcp add --transport http blender http://127.0.0.1:8766/mcp
codex mcp add blender --url http://127.0.0.1:8766/mcp
```

Use the **blender-mcp** skill and ask the agent to inspect the selected object and return an actual camera image or live viewport capture. Run the client and server under the same trusted local user. To use a different interpreter, use its absolute executable path when registering the server.

## Tools and semantics

| Tools | Behavior |
| --- | --- |
| `inspect_scene`, `observe` | Inspect the active selection, look up an object by session ID, page through scenes and collections, and return camera or viewport PNG image blocks. Use `detail="summary"` to browse and `detail="edit"` to get layer measurements and a write guard. |
| `verify_edit` | Compare two retained camera captures. Check where an edit appeared, enforce changed-pixel limits for protected regions, and return a difference overlay without re-rendering. |
| `apply_layer`, `move_layer`, `update_layer` | Add, translate or refine an editable image-mask shader group. Local object coordinates account for scene units and object scale. The depth window and outward-facing surface test constrain projection. Object-linked material slots keep shared source data isolated. |
| `list_addons`, `enable_addon` | Inventory installed modules and activate an installed module in the running process. Discover capabilities by registered operator. |
| `import_svg`, `bool_difference`, `node_texture` | Import curves, add an editable cut with a verified cutter and stack position, and run Node Wrangler on a named node in an Object Shader Editor. |
| `create_gear_train` | Add an animated spur gear assembly with involute teeth, pitch spacing and a reported transmission ratio. Set `studio=false` to build the mechanism without the presentation cameras, lights and backdrop. |
| `set_reference_spec`, `compare_reference`, `finalize_asset` | Compare evaluated geometry against annotated orthographic alpha masks. Report silhouette IoU, landmark offsets and overlays. Depth comparison uses a side camera along world X and an expected size along world Y. |
| `job_status`, `cancel_job`, `checkpoint`, `rollback` | Track or retrieve work across MCP reconnects. Cancel a queued job, write a separate scene copy or roll back scoped edits in reverse order. |
| `verify_glb` | Export mesh and empty hierarchies, reimport into a temporary clean scene, then report differences in canonical triangle geometry, transforms, bounds, parenting and supported material channels. Report projected layers and unsupported shaders as needing baking. |

For normal writes use a fresh write guard from `inspect_scene` and a unique job ID. The same job ID and arguments retrieve the retained result without reapplying the write. The bridge keeps 48 recent jobs (24 MiB of retained result images) and up to 4096 expired IDs in the Blender process. Opening another file changes the scene session identifier. Stopping Blender discards in-memory jobs and rollback history. Render and import operators may block the GUI while running. The scene checkpoint and GLB export write only to the explicit local destination.

The checkpoint copies the scene to a new file and remaps relative asset paths against the destination directory. External assets remain external. The live file and its paths are preserved.

A raster image is interpreted using Blender's normal image color management. Use explicit alpha for color and roughness masks. Width describes the physical width of the projected image rectangle, including any transparent padding. The supported projection uses orthogonal local axes and a Principled BSDF directly connected to Material Output. The reference tool expects prepared annotations and tests geometric agreement. Use camera images for finish and lighting. The GLB verifier currently covers static assets with commonly used Principled PBR inputs.

### Verify the visible scope of an edit

Capture named cameras with `observe` and keep its job ID. Use those cameras in the edit's `view` and keep the new job ID. Pass both IDs and your region checks to `verify_edit`.

```json
{
  "before_job_id": "before",
  "after_job_id": "lids-move",
  "checks": [
    {
      "camera": "Top",
      "regions": [
        {"name": "Target", "box": [0, 0, 0.5, 1], "min_changed_pixels": 100},
        {"name": "Other lid", "box": [0.51, 0, 1, 1], "max_changed_pixels": 0}
      ]
    },
    {
      "camera": "Underside",
      "regions": [{"name": "Whole view", "max_changed_pixels": 0}]
    }
  ]
}
```

Boxes use normalized top-left coordinates. Omitting the box checks the full view. The default threshold of zero checks every visible pixel exactly. Raise `pixel_threshold` to account for render noise when a tolerance is acceptable. The response gives pixel counts and bounds per view and region, along with a `passed` verdict. In the optional overlay, pink marks changed pixels, green outlines mark passed checks and yellow outlines mark failed checks. Set `include_overlays=false` to receive metrics only.

Use the same Blender session, scene, frame, camera projection and render settings for both jobs. Frames are available for as long as the bridge retains their job results. The verdict applies to visible pixels in those captured views and regions. Use scene inspection when the check concerns geometry, occluded objects or material data. Update both the Blender add-on and the external MCP server for this tool.

## Run the wider acceptance check

The bundled Blender package has SVG Import and Node Wrangler. To reproduce the tested Bool Tool integration, fetch its GPL-2.0-or-later Blender upstream legacy add-on. The test installs it into an isolated Blender profile and activates it through MCP.

```sh
mkdir -p work
curl -fsSLo work/object_boolean_tools.py \
  'https://raw.githubusercontent.com/blender/blender-addons/c2525e3054db5dc2dacb04735a9b19373f9d306e/object_boolean_tools.py'
printf '%s  %s\n' \
  2f24c545111329f030a2d35b2890db69bff246e564d968f01140b036b554c3a0 \
  work/object_boolean_tools.py | sha256sum -c -
python tests/acceptance.py --bool-addon work/object_boolean_tools.py
python tests/acceptance.py --gui --bool-addon work/object_boolean_tools.py --out work/acceptance-gui
```

Use an existing desktop display for the GUI run or prefix the last command with `xvfb-run -a`. Use `--blender /path/to/blender` on either test command to choose the version. Add `--codex` for Codex app-server and its model-backed turn. Add `--claude [executable]` for Claude Code's native connection and its model-backed turn. `--require-agents` runs both and exits unsuccessfully if either model-backed run lacks evidence of the MCP calls. The report is written even when a model requires sign-in. The SDK tests run independently of either provider.

```sh
python tests/scalability.py --out work/scalability
python tests/acceptance.py --require-agents --bool-addon work/object_boolean_tools.py --out work/model-acceptance
```

The scalability test creates 5,000 objects, including 200 instances of a shared 25,600-quad mesh. It measures the median of five repeated inspections over HTTP, checks paging over stdio, rejects a guard after a direct shared-mesh change, then edits and rolls back a single instance.

Validated operator contracts are `import_curve.svg`, `btool.boolean_diff` from Bool Tool 0.4.1 and `node.nw_add_texture`. Add-on discovery can find other versions and reports the registered contracts. To reproduce the active editor, the fixture creates an ordinary Shader Editor in the GUI workspace before the Node Wrangler call.

## Verification on 25 November 2026

Blender 4.3.2, Python 3.12.14 and MCP SDK 1.29.0 passed the background and GUI acceptance runs. The GUI run installed the add-on from a ZIP into an isolated Blender profile and started it through its operator using Add-on Preferences. Both transports delivered camera renders. Streamable HTTP also returned a nonblank live Solid-mode viewport capture after the decal edits.

| Check | Observed |
| --- | --- |
| Two curved lids | Width stayed 40.00 mm after a 3.00 mm local X move. The image centroid moved 2.93 mm. The layer retained its ID, and the neighbor and underside had zero changed pixels. |
| Editable operations | SVG Import produced a curve. Enabled Bool Tool 0.4.1 created an editable difference at index 0. Node Wrangler added Image Texture, Mapping and Texture Coordinate in the Shader Editor. Its poll rejected the Viewport. |
| Recovery | The second client retrieved an in-flight edit after disconnect. The move applied once. The suite checked queued cancellation, a stale target, a failed capture with compensation, and rollback. A checkpoint in a different directory reopened with the layer intact and loaded a relative external image. |
| Reference | Detected the missing screw, a port shift of 10% of frame width, and 90 mm depth against a 60 mm target. Corrected front and side IoU were 0.988. Depth was unknown with the side view removed. |
| GLB | Textured hierarchy reimported with matching transforms, triangle geometry and decoded base-color texture. Export of the projected layer was marked as needing baking. |
| Clients | Codex app-server discovered the skill and received a 245 × 147 PNG from Blender. Claude Code confirmed its local MCP server connection. The Codex model requested sign-in after a credential-refresh failure. Claude Code requested sign-in before its model turn. |

In the GUI sample, three 700 × 420 camera views took 3.91 s to capture and returned 995 kB of PNGs. One 350 × 210 view took 0.32 s and returned 88 kB. Blender spent 5.88 ms applying the layer and 0.31 ms moving it, before image capture. The test JSON includes queue and client wall times, image packing, and estimated MCP transmission and decoding overhead. The small fixture showed no clear processing-time difference between selection-only and full inspection.

For the two-view edit verification, the background acceptance client received both overlays in 133 ms with 127 kB of PNGs. A fresh render of the same two views took 2.52 s and returned 634 kB. A metrics-only verification took 96 ms. The suite checked a failed protected region, unavailable frames, mismatched resolutions, and camera movement at the original resolution.

The wider acceptance suite passed on Blender 4.3.2, 4.5.5 LTS, and 5.2.3 LTS. The 5.2.3 GUI run also exercised live viewport capture and Node Wrangler in the Shader Editor on Xvfb. In all three versions, the neighbor and underside remained pixel-identical in the decal fixture.

On the 5,000-object fixture, the median for a detailed 200-object collection page over HTTP went from 2,096 ms to 94 ms. The summary page took 23 ms. Each synchronous read fingerprints a shared mesh and shader graph once. A later request rereads them, so writes with old guards were rejected after a manual shared-mesh edit or an edit inside a nested shader group. The test then applied and rolled back a 40 mm layer on one instance while the neighboring instance kept its shared material.

Reports are under `work/quality-release-4.3/`, `work/quality-release-4.5/`, `work/quality-release-5.2-gui/`, and `work/quality-scale-final/` in the development workspace. The earlier baseline is under `work/scalability-before/`.
