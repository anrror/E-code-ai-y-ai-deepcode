# DeepCodeX installed deployment (this Windows machine)

- Install root: `D:\Program\DeepCode` (`deepcode-desktop.exe`, `app-server\`, `uninstall.exe`).
- Desktop is Tauri: the frontend is **embedded in `deepcode-desktop.exe`** (from `desktop/dist` at build time). Only rebuilding the exe changes the desktop UI.
- Sidecar: `app-server\deepcode-app-server.exe` + `app-server\_internal\` (PyInstaller onedir). It bundles `_internal\app_server\web_assets\` (the browser client UI).
- The sidecar exe is file-locked while running: renaming/replacing `app-server\` fails with WinError 5. An update needs the app closed.
- Rebuild/deploy tools:
  - `.venv\Scripts\python.exe` (3.12) + `desktop\build\sidecar\.venv` (PyInstaller 6.21).
  - Build: `DEEPCODE_TARGET_TRIPLE=x86_64-pc-windows-msvc DEEPCODE_SIDECAR_PYTHON=<venv python> python desktop/scripts/build-sidecar.py`.
  - Web assets: `node desktop/node_modules/vite/bin/vite.js build --mode web` (writes `app_server/web_assets`).
  - Node: `D:\Program Files (x86)\nvm\v22.19.0\node.exe` (not on PATH).
- Rust desktop rebuild is **blocked** here: no `link.exe`/`cl.exe` on PATH and the Windows SDK `Lib\...\um\x64\kernel32.lib` is absent; `~/.cargo/config.toml` routes crates.io to `127.0.0.1:7891`, which is down. An offline cargo home with cached crates exists at `%TEMP%\opencode\cargo-home`.
- Deployment pattern used: stage `app-server.next`, verify `--verify-runtime`, then swap via `D:\Program\DeepCode\apply-update.ps1` (waits for app exit, backs up to `app-server.backup-<ts>`, rolls back on probe failure, relaunches). A detached watcher started through `Win32_Process.Create` survives the desktop's job object.
- Tooling: the `bash` tool times out for every command; use code mode + `subprocess.run` instead.
