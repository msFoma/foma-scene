<div align="center">

<img src="docs/assets/banner-pixel.webp" width="100%" alt="Foma Scene · Blender MCP. Пиксельный баннер с синей, стальной и медной шестернями.">

<p><a href="README.md"><img src="docs/assets/header/lang-en.off.svg" height="34" alt="English"></a> <img src="docs/assets/header/lang-ru.on.svg" height="34" alt="Русский, текущая страница"></p>

<p><strong>Редактируйте открытую сцену Blender из Codex или Claude Code и проверяйте правки по рендерам и пиксельным сравнениям.</strong></p>

<p><a href="#install"><img src="docs/assets/header/nav-install.ru.svg" height="42" alt="Установка"></a> <a href="#demos"><img src="docs/assets/header/nav-demo.ru.svg" height="42" alt="Демо"></a> <a href="#verification"><img src="docs/assets/header/nav-checks.ru.svg" height="42" alt="Проверки"></a></p>

<p><a href="#project-structure">Устройство проекта</a></p>

</div>

<a id="install"></a>

## Установка

### 1. Установите зависимости

- **Blender 4.3+**. Скачайте и установите обычную версию для вашей системы. [Скачать Blender](https://www.blender.org/download/)
- **MCP-клиент**. Установите Codex CLI или Claude Code и войдите в аккаунт. Проверьте в терминале, что команда `codex --version` или `claude --version` выводит версию. [Codex CLI](https://developers.openai.com/codex/cli/) · [Claude Code](https://code.claude.com/docs/en/setup)
- **Git и Python 3.11+**. Команды для вашей системы приведены ниже.

### 2. Подготовьте систему

[Linux](#install-linux) · [macOS](#install-macos) · [Windows](#install-windows)

<a id="install-linux"></a>

<details>
<summary><strong>Linux</strong> · Bash</summary>

На Ubuntu 24.04 или Debian 12+ установите Git, Python и поддержку виртуальных окружений. В других дистрибутивах используйте их менеджер пакетов.

```bash
sudo apt update
sudo apt install git python3 python3-venv
python3 --version
```

Если Blender скачан архивом, распакуйте его. При запуске установщика добавьте путь к исполняемому файлу. Например, `--blender "$HOME/apps/blender/blender"`.

</details>

<a id="install-macos"></a>

<details>
<summary><strong>macOS</strong> · Терминал</summary>

Переместите Blender.app в **Программы**. Если у вас установлен Homebrew, выполните `brew install git python`. Git и Python также можно установить с официальных сайтов. [Git](https://git-scm.com/downloads) · [Python](https://www.python.org/downloads/macos/)

```bash
python3 --version
git --version
```

Если приложение лежит в другой папке или называется иначе, укажите путь к исполняемому файлу внутри приложения. Например, `--blender "/Applications/Blender LTS.app/Contents/MacOS/Blender"`.

</details>

<a id="install-windows"></a>

<details>
<summary><strong>Windows</strong> · PowerShell</summary>

Установите версии Blender и MCP-клиента для Windows. Если доступен WinGet, установите Git и Python командами:

```powershell
winget install --id Git.Git --exact
winget install --id Python.Python.3.12 --exact
```

Можно воспользоваться и официальными установщиками. При ручной установке Python включите лаунчер, если установщик предлагает такой вариант. [Git для Windows](https://git-scm.com/download/win) · [Python для Windows](https://www.python.org/downloads/windows/)

Откройте новое окно PowerShell, чтобы обновился PATH, и выполните:

```powershell
py -3 --version
git --version
```

Если команда `py` недоступна, но `python --version` показывает 3.11 или новее, используйте `python`. Если скрипт не нашёл Blender, добавьте `--blender` и путь к вашему `blender.exe`. Например, `--blender "C:\Program Files\Blender Foundation\Blender 4.5\blender.exe"`.

</details>

### 3. Скачайте Foma Scene и выберите клиента

Закройте Blender. Склонируйте репозиторий и перейдите в его папку. На Windows выполняйте команды в PowerShell.

```sh
git clone https://github.com/msFoma/foma-scene.git
cd foma-scene
```

Установщик включит аддон в Blender и зарегистрирует сервер `foma-scene` в выбранном клиенте.

<table>
  <tr>
    <th><a href="#setup-codex">Codex CLI</a></th>
    <th><a href="#setup-claude">Claude Code</a></th>
  </tr>
  <tr>
    <td align="center"><a href="#setup-codex"><img src="docs/assets/client-codex.webp" width="320" alt="Синий терминал Codex в пиксельном стиле"><br>К настройке</a></td>
    <td align="center"><a href="#setup-claude"><img src="docs/assets/client-claude.webp" width="320" alt="Медный терминал Claude Code в пиксельном стиле"><br>К настройке</a></td>
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
<summary>Windows · PowerShell</summary>

```powershell
py -3 scripts/setup_scene.py --client codex
codex mcp list
```

</details>

Команда `codex mcp list` должна показать `foma-scene` со статусом `enabled`. После подключения Blender запустите `codex`.

<a id="setup-claude"></a>

#### Claude Code

**Linux / macOS**

```bash
python3 scripts/setup_scene.py --client claude
claude mcp list
```

<details>
<summary>Windows · PowerShell</summary>

```powershell
py -3 scripts/setup_scene.py --client claude
claude mcp list
```

</details>

Команда `claude mcp list` должна показать `foma-scene` со статусом `Connected`. После подключения Blender запустите `claude`.

### 4. Подключите Blender

1. Откройте Blender. Наведите курсор на **3D Viewport**, нажмите **N**, откройте вкладку **Blender MCP** и нажмите **Connect**. Когда мост запустится, кнопка сменится на **Disconnect**.
2. Запустите выбранный клиент. Выделите объект в Blender и попросите агента:

   > Через Foma Scene проверь выделенный объект и покажи снимок из окна 3D Viewport.

<details>
<summary>Если подключение не работает</summary>

- **Вкладки Blender MCP нет.** Перезапустите Blender и проверьте, что скрипт установил аддон в ту же версию программы. Найдите **Foma Scene · Blender MCP** в **Preferences → Add-ons**.
- **Команда клиента не найдена.** Откройте новый терминал и проверьте `codex --version` или `claude --version`. При запуске без `--client` скрипт выведет команды регистрации.
- **Сервер не видит Blender.** Запустите клиент и Blender в одной локальной среде. Для этой установки на Windows запускайте оба приложения в Windows. Убедитесь, что в боковой панели видна кнопка **Disconnect**. Если меняли токен или порт моста, закройте Blender и запустите установку ещё раз.

[Ручная установка и Streamable HTTP (EN)](docs/guide.md#manual-setup-and-local-http)

</details>

<a id="demos"></a>

## Демо

<img src="docs/assets/gear-train.webp" width="100%" loading="lazy" alt="Рендер Blender с шестернями на 18, 30 и 54 зуба на металлическом основании">

**Анимированная передача 3:1.** В пустой сцене попросите Foma Scene создать шестерни на 18, 30 и 54 зуба и показать рендер камеры. За один оборот входной шестерни выходная поворачивается на 120° в ту же сторону.

<a id="project-structure"></a>

## Устройство проекта

<p><strong>Codex / Claude Code → MCP → Blender</strong></p>

Клиент запускает локальный MCP-сервер. Сервер передаёт запрос аддону в открытой сессии Blender и возвращает данные и изображения клиенту.

<table>
  <thead>
    <tr><th scope="col">Каталог</th><th scope="col">За что отвечает</th></tr>
  </thead>
  <tbody>
    <tr><th scope="row"><a href="blender_bridge/"><code>blender_bridge/</code></a></th><td>Аддон выполняет операции в главном потоке Blender, снимает кадры и хранит историю правок.</td></tr>
    <tr><th scope="row"><a href="blender_mcp/"><code>blender_mcp/</code></a></th><td>MCP-сервер принимает команды клиентов по stdio или локальному Streamable HTTP.</td></tr>
    <tr><th scope="row"><a href="scripts/"><code>scripts/</code></a></th><td>Установщик создаёт окружение Python, включает аддон и регистрирует MCP в выбранном клиенте.</td></tr>
    <tr><th scope="row"><a href="skills/blender-mcp/"><code>skills/blender-mcp/</code></a></th><td>Навык описывает действия агента и проверку результата в Blender.</td></tr>
    <tr><th scope="row"><a href="tests/"><code>tests/</code></a></th><td>Сценарии проверяют MCP и операции в настоящем Blender.</td></tr>
    <tr><th scope="row"><a href="docs/"><code>docs/</code></a></th><td>Справочник инструментов, результаты проверок и иллюстрации.</td></tr>
  </tbody>
</table>

<a id="verification"></a>

## Инструменты и проверки

Foma Scene поддерживает редактируемые декали, рендеры камер и снимки 3D Viewport, сравнение с референсами, операции совместимых аддонов, откат и проверку GLB повторным импортом.

[Справочник инструментов (EN)](docs/guide.md#tools-and-semantics) · [Навык для агентов (EN)](skills/blender-mcp/SKILL.md) · [Результаты тестов (EN)](docs/guide.md#verification-on-25-november-2026)
