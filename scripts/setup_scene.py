"""Install the Blender add-on and register a local Foma Scene MCP server."""
import argparse
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import venv
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
ENVIRONMENT = ROOT / ".venv"
CONFIG = ENVIRONMENT / "foma-scene-bridge.json"


def find_blender(explicit):
    if explicit:
        path = shutil.which(explicit) or Path(explicit).expanduser()
        if Path(path).is_file():
            return Path(path).resolve()
        raise ValueError(f"Blender executable not found: {explicit}")
    if executable := shutil.which("blender"):
        return Path(executable)
    if sys.platform == "darwin":
        candidates = [Path("/Applications/Blender.app/Contents/MacOS/Blender")]
    elif os.name == "nt":
        install = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        candidates = sorted((install / "Blender Foundation").glob("Blender */blender.exe"),
                            reverse=True)
    else:
        candidates = []
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise ValueError("Blender not found. Pass --blender with the path to its executable.")


def client_command(client, executable, server):
    if client == "claude":
        return [executable, "mcp", "add", "--scope", "user", "--transport", "stdio",
                "foma-scene", "--", *server]
    return [executable, "mcp", "add", "foma-scene", "--", *server]


def register_client(client, executable, server):
    if client == "claude":
        result = subprocess.run([executable, "mcp", "get", "foma-scene"],
                                capture_output=True, text=True, errors="replace")
        fields = {line.strip() for line in result.stdout.splitlines()}
        if result.returncode == 0 and {
            "Scope: User config (available in all your projects)",
            "Type: stdio",
            f"Command: {server[0]}",
            f"Args: {' '.join(server[1:])}",
        } <= fields:
            print("Foma Scene is already registered in Claude Code.", flush=True)
            return
    subprocess.run(client_command(client, executable, server), check=True)


def install(blender, clients):
    if sys.version_info < (3, 11):
        raise ValueError("Foma Scene requires Python 3.11 or newer.")
    print("Preparing the local Python environment…", flush=True)
    if not (ENVIRONMENT / "pyvenv.cfg").is_file():
        venv.EnvBuilder(with_pip=True).create(ENVIRONMENT)
    python = ENVIRONMENT / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    subprocess.run([str(python), "-m", "pip", "install", "-e", str(ROOT)], check=True)

    print("Installing and enabling the Blender add-on…", flush=True)
    archive = ENVIRONMENT / "foma-scene-addon.zip"
    with ZipFile(archive, "w", compression=ZIP_DEFLATED) as zip_file:
        for source in sorted((ROOT / "blender_bridge").rglob("*.py")):
            zip_file.write(source, source.relative_to(ROOT).as_posix())
    subprocess.run([str(blender), "--background", "--python-exit-code", "1",
                    "--python", str(ROOT / "scripts/install_addon.py"), "--",
                    str(archive), str(CONFIG)], check=True)

    server = [str(python), "-m", "blender_mcp.server", "--bridge-config", str(CONFIG)]
    for client, executable in clients.items():
        print(f"Registering Foma Scene in {client}…", flush=True)
        register_client(client, executable, server)
    if not clients:
        for client in ("codex", "claude"):
            command = client_command(client, client, server)
            print(subprocess.list2cmdline(command) if os.name == "nt" else shlex.join(command))
    print("Reopen Blender if it was running. In the 3D Viewport, press N, "
          "open Blender MCP, and click Connect. Start a fresh MCP client session.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blender", help="Path to the Blender executable")
    parser.add_argument("--client", choices=("codex", "claude"), action="append", default=[],
                        help="Register in an installed client (repeat for both)")
    args = parser.parse_args()
    try:
        blender = find_blender(args.blender)
        clients = {}
        for client in args.client:
            executable = shutil.which(client)
            if not executable:
                raise ValueError(f"{client} is not on PATH. Install it or run setup without --client.")
            clients[client] = executable
        install(blender, clients)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Foma Scene setup failed: {exc}\n")


if __name__ == "__main__":
    main()
