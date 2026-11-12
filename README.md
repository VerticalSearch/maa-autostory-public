# MAA AutoStory - source snapshot

Source-only snapshot of **MAA 自动推图助手** ("MAA AutoStory"), a local Windows
companion for MAA (MaaAssistantArknights) that automates Arknights stage
progression through MAA's Python API and ADB.

- Snapshot date: **2026-11-12**
- Application version: **0.9.44** (`autostory/__init__.py`, matching the
  local `active-release.json`)
- Repository: `VerticalSearch/maa-autostory` (private)
- Original layout: an `AutoStory` directory next to the MAA runtime
  (`MAA-v6.9.5-win-x64/AutoStory`)

This is a **source checkout, not a runnable release**. The packaged `App*`
directories, executables, DLLs, models, runtime data and release evidence are
not included. The checked-in launchers still describe the packaged Windows
layout and will not work from this checkout until an app is built.

## Included source

- Root Python entry points: `desktop.py`, `desktop_ui.py`, `server.py`,
  `worker.py`, `launch.pyw`, `refresh_operators.py`, `build_catalogue.py`,
  `verify_raid.py`, `verify_trial.py`
- Windows launchers: `启动助手.ps1`, `启动GPU版本.ps1`, the three `.vbs`
  helpers
- `requirements.txt`, `active-release.json`, `job-sources.json`
- `autostory/` - controller, navigation, vision/OCR, jobs, recovery, state
- `web/index.html` - loopback dashboard UI
- `assets/` - UI icons, zone icons and design source art
- `webview/SDK-PROVENANCE.json` - WebView2 SDK provenance metadata
- `packaging/` - PyInstaller spec, build/install/audit scripts and the C#
  WebView2 host source (`WebHost.cs`)
- `tests/` - unittest modules plus OCR fixtures
- Project documentation at the repository root (`README_APP.md` is the original
  application README; `FULLCOUNTER_0944.md` is the latest release note)

## Not included

- `App`, `App-0.9.*` and any packaged release/binary directory
- `_tmp`, `_build*`, `.venv*`, `__pycache__`, `.git`, `node_modules`,
  `_internal`, `bin`, `obj`, `build`, `dist`
- `runtime/`, `data/`, `jobs-import/`, `output/`, `evidence/`, `research/`,
  `release/`
- Compiled executables, DLLs, PYD files, archives and model weights
- Screenshots/capture evidence outside the small OCR test fixtures
- Real `settings.json`, runtime credentials, cookies, tokens and private keys
- The prebuilt `webview/*.exe` and `webview/*.dll` host binaries
- `CORE_RELEASE_STATUS.json` (a stale 0.9.36 hash manifest)

`job-sources.json` is present as source configuration, but its optional
`data/job-source-backup.json` snapshot is part of the excluded `data/` tree.

## Requirements

- Windows 10/11 x64 (the code uses Win32 APIs and WebView2)
- A Python 3 interpreter supported by the dependencies below
- MAA 6.20.1 runtime with its `asst` Python package, DLLs and resources; the
  source checks for MAA 6.20.1 and expects MAA to be the parent directory of
  `AutoStory`
- ADB plus an Android emulator running Arknights at 16:9, normally 1280x720
- Microsoft Edge WebView2 Runtime for the desktop window
- For the C# host only: .NET Framework `csc.exe` and the Microsoft
  WebView2 SDK assemblies (not vendored here)

Install the Python dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

`requirements.txt` contains `numpy`, `opencv-python` and
`rapidocr-onnxruntime`. MAA's own Python environment is an external
prerequisite; it is not installed by this file.

## Setup from source

1. Place this checkout at `<MAA root>/AutoStory` so that `autostory.common`
   resolves `ROOT.parent` to the MAA root, or set `AUTOSTORY_ROOT` to the
   checkout directory and keep MAA next to it.
2. Install the Python dependencies in an isolated environment.
3. Copy `settings.example.json` to `settings.json` and adjust `device`,
   `port`, `adb_path` (if needed) and run limits. `settings.json` is ignored
   by Git.
4. Make MAA's `asst` package importable, start MAA once, and confirm the
   emulator is online and the game is logged in.
5. Keep MAA's daily tasks idle while this tool owns the device.

`settings.example.json` is the safe default configuration. It contains only
generic loopback/device defaults; no account ID, token or local absolute path.

## Entry points

```powershell
python desktop.py                 # desktop window + loopback service
python server.py                  # loopback service only (127.0.0.1)
python worker.py scan --chapter CW-EX
python worker.py run-all
python launch.pyw                 # background service without a console
```

These commands assume the MAA prerequisite above. They can issue real game
actions; use a test emulator until the configuration is verified. This
snapshot was not started, and no hardware, ADB, screen automation, game,
model service or live API was exercised.

## Build

The packaged app is produced with PyInstaller:

```powershell
python -m pip install pyinstaller
powershell -ExecutionPolicy Bypass -File packaging/build_app.ps1 -AppName App-0.9.44
```

The C# WebView2 host is compiled directly with `csc.exe` (there is no
`.csproj`) by `packaging/build_webhost.ps1`; its output and SDK DLLs go to
`_tmp/webhost-090`, which is ignored. See `webview/README.md`.

Build outputs must not be committed.

## Tests and offline validation

The test suite is included, but it is not a self-contained green suite: some
modules reference `release/evidence/...` fixtures and
`tests/FIXTURES_MANIFEST.json`/`bad_assets` that were outside the permitted
source scope. No claim of a full test pass is made here.

For this snapshot, 342 Python files were parsed with `ast.parse`
(0 failures), 59 PowerShell files were parser-checked
(0 failures), and 6 JSON files were parsed. No
application startup, unit-test run, browser run, C# compilation, hardware
access or network request was performed. Full evidence is in
`reports/maa/report.json` and `reports/maa/manifest.json` on the staging host.

## Security and privacy

The original `settings.json` is excluded. `settings.example.json` contains no
credentials. Do not commit a real `settings.json`, ADB keys, cookies, tokens or
logs. The private repository may still contain machine-specific absolute paths
inside a few development/launcher files; these are flagged by filename in the
staging report and can be sanitized before publication if desired.

## Snapshot provenance

- Source tree: read-only local MAA `AutoStory` install
- Snapshot date: 2026-11-12
- Version: 0.9.44
- Copied source files: 501 (25607085 bytes)
- Generated repository files: `README.md`, `.gitignore`, `webview/README.md`,
  `settings.example.json`
- Hash manifest: `reports/maa/manifest.json` (staging host, not committed)

No Git repository, commit, remote or push was created by this staging step.
