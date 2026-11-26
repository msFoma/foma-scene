<div align="center">

<img src="docs/assets/banner-pixel.webp" width="100%" alt="Foma Scene · Blender MCP. A pixel art banner with blue, steel and copper gears.">

<p><img src="docs/assets/header/lang-en.on.svg" height="34" alt="English, current page"> <a href="README.ru.md"><img src="docs/assets/header/lang-ru.off.svg" height="34" alt="Русский"></a></p>

<p><strong>Edit your open Blender scene from Codex or Claude Code. Verify changes with camera renders and pixel comparisons.</strong></p>

<p><a href="#install"><img src="docs/assets/header/nav-install.en.svg" height="42" alt="Install"></a> <a href="#demos"><img src="docs/assets/header/nav-demo.en.svg" height="42" alt="Demos"></a> <a href="#verification"><img src="docs/assets/header/nav-checks.en.svg" height="42" alt="Verification"></a></p>

<p><a href="#project-structure">Project structure</a></p>

</div>

<a id="install"></a>

## Installation

### 1. Install the prerequisites

- **Blender 4.3+**. Download and install the desktop version. [Blender downloads](https://www.blender.org/download/)
- **One MCP client**. Install the Codex CLI or Claude Code, then sign in. Check that `codex --version` or `claude --version` works in your terminal. [Codex CLI](https://developers.openai.com/codex/cli/) · [Claude Code](https://code.claude.com/docs/en/setup)
- **Git and Python 3.11+**. Use the commands for your OS below.

### 2. Prepare your system

[Linux](#install-linux) · [macOS](#install-macos) · [Windows](#install-windows)

<a id="install-linux"></a>

<details>
<summary><strong>Linux</strong> · Bash</summary>

On Ubuntu 24.04 or Debian 12+, install Git, Python and support for virtual environments. On other distributions, use your package manager for the same dependencies.

```bash
sudo apt update
sudo apt install git python3 python3-venv
python3 --version
```

If you downloaded Blender as an archive, extract it and pass the executable path when you run setup. For example, use `--blender "$HOME/apps/blender/blender"`.

</details>

<a id="install-macos"></a>

<details>
<summary><strong>macOS</strong> · Terminal</summary>

Move Blender.app to **Applications**. With Homebrew installed, use `brew install git python`. You can also install Git and Python from their official download pages. [Git](https://git-scm.com/downloads) · [Python](https://www.python.org/downloads/macos/)

```bash
python3 --version
git --version
```

If the app is elsewhere or has a different name, pass the full path to the executable inside the app. For example, `--blender "/Applications/Blender LTS.app/Contents/MacOS/Blender"`.

</details>

<a id="install-windows"></a>

<details>
<summary><strong>Windows</strong> · PowerShell</summary>

Install the Windows versions of Blender and your MCP client. With WinGet available, run the following commands to install Git and Python.

```powershell
winget install --id Git.Git --exact
winget install --id Python.Python.3.12 --exact
```

You can also use the official installers. When installing Python manually, enable the launcher if offered. [Git for Windows](https://git-scm.com/download/win) · [Python for Windows](https://www.python.org/downloads/windows/)

Open a new PowerShell window so it picks up the updated PATH, then run:

```powershell
py -3 --version
git --version
```

If `py` is unavailable but `python --version` shows 3.11 or newer, use `python`. If setup cannot find Blender, add `--blender` with the path to your `blender.exe`, for example `--blender "C:\Program Files\Blender Foundation\Blender 4.5\blender.exe"`.

</details>

### 3. Download Foma Scene and choose a client

Close Blender. Clone the repository and change to its directory. On Windows, run these commands in PowerShell.

```sh
git clone https://github.com/msFoma/foma-scene.git
cd foma-scene
```

Setup enables the Blender add-on and registers the `foma-scene` server with the chosen client.

<table>
  <tr>
    <th><a href="#setup-codex">Codex CLI</a></th>
    <th><a href="#setup-claude">Claude Code</a></th>
  </tr>
  <tr>
    <td align="center"><a href="#setup-codex"><img src="docs/assets/client-codex.webp" width="320" alt="Blue pixel-art terminal for Codex"><br>Setup instructions</a></td>
    <td align="center"><a href="#setup-claude"><img src="docs/assets/client-claude.webp" width="320" alt="Copper pixel-art terminal for Claude Code"><br>Setup instructions</a></td>
  </tr>
</table>

<a id="setup-codex"></a>

#### Codex CLI

**Linux / macOS**

```bash
python3 scripts/setup_scene.py --client codex
codex mcp list
```

<details>
<summary>Windows · In PowerShell</summary>

```powershell
py -3 scripts/setup_scene.py --client codex
codex mcp list
```

</details>

`codex mcp list` should show `foma-scene` as `enabled`. After connecting Blender, start `codex`.

<a id="setup-claude"></a>

#### Claude Code

**Linux / macOS**

```bash
python3 scripts/setup_scene.py --client claude
claude mcp list
```

<details>
<summary>Windows · In PowerShell</summary>

```powershell
py -3 scripts/setup_scene.py --client claude
claude mcp list
```

</details>

`claude mcp list` should show `foma-scene` as `Connected`. After connecting Blender, start `claude`.

### 4. Connect Blender

1. Open Blender. Hover over the **3D Viewport**, press **N**, open **Blender MCP**, and click **Connect**. The button changes to **Disconnect** when Blender is listening.
2. Start your client. Select an object in Blender and ask the agent:

   > Use Foma Scene to inspect the selected object and show me an image from the Blender viewport.

<details>
<summary>If the connection fails</summary>

- **The Blender MCP tab is missing.** Reopen Blender and verify that setup targeted the same Blender installation. In **Preferences → Add-ons**, look for **Foma Scene · Blender MCP**.
- **The client is missing from PATH.** Open a new terminal and check `codex --version` or `claude --version`. Setup without `--client` prints the registration commands.
- **The tool cannot reach Blender.** Keep Blender and the client in the same local OS environment. On Windows, run both on Windows for this setup. Check that the sidebar shows **Disconnect**. If you changed the bridge token or port in Blender, close Blender and rerun setup to sync the settings.

[Manual installation and Streamable HTTP](docs/guide.md#manual-setup-and-local-http)

</details>

<a id="demos"></a>

## See it in action

<img src="docs/assets/gear-train.webp" width="100%" loading="lazy" alt="Blender render of the generated 18-, 30-, and 54-tooth gears on a machined base">

**Animated 3:1 gear train.** In a blank scene, ask Foma Scene to create gears with 18, 30 and 54 teeth and return a camera render. One input turn advances the output by 120° in the same direction.

<a id="project-structure"></a>

## Project structure

<p><strong>Codex / Claude Code → MCP → Blender</strong></p>

The client starts a local MCP server. The server passes requests to the add-on in the open Blender session and returns data and images to the client.

<table>
  <thead>
    <tr><th scope="col">Directory</th><th scope="col">Responsibility</th></tr>
  </thead>
  <tbody>
    <tr><th scope="row"><a href="blender_bridge/"><code>blender_bridge/</code></a></th><td>The add-on runs operations on Blender’s main thread, captures frames, and keeps edit history.</td></tr>
    <tr><th scope="row"><a href="blender_mcp/"><code>blender_mcp/</code></a></th><td>The MCP server receives client requests over stdio or local Streamable HTTP.</td></tr>
    <tr><th scope="row"><a href="scripts/"><code>scripts/</code></a></th><td>Setup creates the Python environment, enables the add-on, and registers MCP with the chosen client.</td></tr>
    <tr><th scope="row"><a href="skills/blender-mcp/"><code>skills/blender-mcp/</code></a></th><td>The agent skill explains which tools to use and how to check results in Blender.</td></tr>
    <tr><th scope="row"><a href="tests/"><code>tests/</code></a></th><td>Tests exercise the MCP transports and operations in a real Blender session.</td></tr>
    <tr><th scope="row"><a href="docs/"><code>docs/</code></a></th><td>Tool reference, test evidence, and illustrations.</td></tr>
  </tbody>
</table>

<a id="verification"></a>

## Tools and verification

Foma Scene supports editable decals, camera and viewport captures, comparisons against reference views, supported add-on operators, rollback, and GLB reimport checks.

[Tool reference](docs/guide.md#tools-and-semantics) · [Agent skill](skills/blender-mcp/SKILL.md) · [Test results](docs/guide.md#verification-on-25-november-2026)
