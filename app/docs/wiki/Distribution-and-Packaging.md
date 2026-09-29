# Distribution and Packaging

## Overview

AutoApply is distributed as a desktop application using [electron-builder](https://www.electron.build/). The build system produces native installers for all three major platforms:

| Platform | Format | File |
|----------|--------|------|
| Windows | NSIS installer | `AutoApply-Setup-x.y.z.exe` |
| macOS | DMG disk image | `AutoApply-x.y.z.dmg` |
| Linux | AppImage | `AutoApply-x.y.z.AppImage` |

Each installer bundles:
- The Electron shell
- The complete Python backend (embedded Python + all dependencies)
- Playwright Chromium browser
- All static assets, templates, and locale files

---

## Build Scripts

The build pipeline uses three custom Node.js scripts in the `electron/` directory:

### generate-icon.js

Generates application icons programmatically from a 1024x1024 PNG source:

```bash
cd electron
npm run icons:generate
```

**Input**: Programmatic 1024x1024 PNG (no external image file needed).
**Output**:
- `build/icon.ico` -- Windows (multi-resolution ICO)
- `build/icon.icns` -- macOS (Apple ICNS bundle)
- `build/icon.png` -- Linux (1024x1024 PNG)

### sync-version.js

Synchronizes the version number from `pyproject.toml` to `electron/package.json`:

```bash
cd electron
npm run sync-version
```

This ensures the Python backend and Electron frontend always report the same version. The script:
1. Reads the `version` field from `pyproject.toml`.
2. Updates the `version` field in `electron/package.json`.
3. Writes the file (no commit).

### bundle-python.js

Bundles a standalone Python environment with all dependencies:

```bash
cd electron
npm run bundle-python
```

This script handles platform-specific Python bundling:

| Platform | Python Distribution | Method |
|----------|-------------------|--------|
| Windows | Python embeddable package | Downloads official Windows embeddable zip from python.org |
| macOS | python-build-standalone | Downloads prebuilt standalone Python from indygreg/python-build-standalone |
| Linux | python-build-standalone | Downloads prebuilt standalone Python from indygreg/python-build-standalone |

The bundling process:
1. Downloads the appropriate Python distribution for the target platform.
2. Extracts to `electron/python-dist/`.
3. Installs all Python dependencies from `pyproject.toml` into the bundled environment.
4. Installs Playwright and downloads Chromium into the bundle.
5. Verifies the installation by running a test import.

---

## Icon Generation

The icon is generated programmatically (no external design tool needed):

```bash
cd electron
npm run icons:generate
```

The script creates a modern, clean application icon with:
- "AA" monogram text
- Gradient background
- Rounded corners
- Multiple resolutions for each platform

Generated files are placed in `electron/build/` and referenced by `electron-builder` configuration.

---

## Version Synchronization

The authoritative version lives in `pyproject.toml`:

```toml
[project]
name = "autoapply"
version = "1.9.0"
```

Before building, sync to Electron:

```bash
cd electron
npm run sync-version
```

This updates `electron/package.json`:
```json
{
  "name": "autoapply",
  "version": "1.9.0"
}
```

The health endpoint (`GET /api/health`) reads the version from the Python package metadata, and the Electron About dialog reads it from `package.json`. Both must agree.

---

## Python Bundling Details

### Windows Embeddable Python

On Windows, the build uses Python's official [embeddable package](https://docs.python.org/3/using/windows.html#the-embeddable-package):

1. Downloads `python-3.11.x-embed-amd64.zip` from python.org.
2. Extracts to `electron/python-dist/`.
3. Enables `pip` by uncommenting the `import site` line in `python311._pth`.
4. Installs `pip` via `get-pip.py`.
5. Installs project dependencies: `python-dist/python.exe -m pip install -r requirements.txt`.
6. Installs Playwright Chromium: `python-dist/python.exe -m playwright install chromium`.

### macOS / Linux (python-build-standalone)

On macOS and Linux, the build uses [python-build-standalone](https://github.com/indygreg/python-build-standalone):

1. Downloads the appropriate build for the architecture (x86_64 or arm64).
2. Extracts to `electron/python-dist/`.
3. Installs dependencies using the bundled pip.
4. Installs Playwright Chromium.

### Runtime Detection

At runtime, `electron/python-backend.js` locates Python in this order:

1. **Bundled Python**: `<app-resources>/python-dist/python.exe` (Windows) or `python-dist/bin/python3` (macOS/Linux).
2. **Local venv**: `venv/Scripts/python.exe` (Windows) or `venv/bin/python3` (for development).
3. **System Python**: Falls back to `python3` on PATH (unlikely to have correct dependencies).

---

## Building Locally

### Prerequisites

- Node.js 18+
- npm 9+
- Python 3.11+ (for the bundle script)
- Platform-specific build tools:
  - **Windows**: Visual Studio Build Tools (for native modules)
  - **macOS**: Xcode Command Line Tools
  - **Linux**: `dpkg-dev`, `fakeroot`

### Build Commands

```bash
cd electron

# Generate icons
npm run icons:generate

# Sync version from pyproject.toml
npm run sync-version

# Bundle Python environment
npm run bundle-python

# Build installer for current platform
npm run dist:win     # Windows .exe
npm run dist:mac     # macOS .dmg
npm run dist:linux   # Linux .AppImage
```

Output is placed in `electron/dist/`.

### Build Configuration

The electron-builder configuration in `electron/package.json`:

```json
{
  "build": {
    "appId": "com.autoapply.app",
    "productName": "AutoApply",
    "directories": {
      "output": "dist"
    },
    "extraResources": [
      "../bot/**/*",
      "../config/**/*",
      "../core/**/*",
      "../db/**/*",
      "../routes/**/*",
      "../static/**/*",
      "../templates/**/*",
      "../app.py",
      "../app_state.py",
      "../run.py",
      "../pyproject.toml"
    ],
    "win": {
      "target": "nsis",
      "icon": "build/icon.ico"
    },
    "mac": {
      "target": "dmg",
      "icon": "build/icon.icns"
    },
    "linux": {
      "target": "AppImage",
      "icon": "build/icon.png"
    }
  }
}
```

---

## CI Release Pipeline

Automated releases are triggered by pushing a version tag:

```bash
git tag v1.9.0
git push origin v1.9.0
```

The GitHub Actions workflow (`.github/workflows/release.yml`):

1. **Trigger**: `push` on tags matching `v*`.
2. **Matrix build**: Runs on `ubuntu-latest`, `macos-latest`, and `windows-latest`.
3. **Steps per platform**:
   - Checkout code.
   - Set up Node.js 18 and Python 3.11.
   - Install dependencies (`npm install`, `pip install -e ".[dev]"`).
   - Generate icons (`npm run icons:generate`).
   - Sync version (`npm run sync-version`).
   - Bundle Python (`npm run bundle-python`).
   - Build installer (`npm run dist:win` / `dist:mac` / `dist:linux`).
   - Upload artifacts.
4. **Create GitHub Release**: Collects all platform artifacts and creates a release with:
   - Release notes (from CHANGELOG.md).
   - Windows `.exe` installer.
   - macOS `.dmg` disk image.
   - Linux `.AppImage`.

---

## extraResources: What Gets Bundled

The `extraResources` configuration specifies which Python source files and assets are included in the packaged application:

| Resource | Purpose |
|----------|---------|
| `bot/**/*` | Bot core, search engines, appliers |
| `config/**/*` | Settings models |
| `core/**/*` | AI engine, filter, scheduler, renderer, i18n |
| `db/**/*` | Database operations |
| `routes/**/*` | Flask blueprints |
| `static/**/*` | CSS, JavaScript modules, locale files |
| `templates/**/*` | Jinja2 HTML template |
| `app.py` | Flask app factory |
| `app_state.py` | Shared state singleton |
| `run.py` | Server entry point |
| `pyproject.toml` | Package metadata (version, dependencies) |

Files **not** bundled:
- `tests/` -- Not needed at runtime.
- `electron/` -- Electron files are bundled by electron-builder itself.
- `.claude/`, `.github/` -- Development/CI only.
- `venv/` -- Replaced by bundled Python in `python-dist/`.

---

## Known Limitations

### Unsigned Binaries

The installers are currently **unsigned**, which triggers platform security warnings:

| Platform | Warning | Workaround |
|----------|---------|------------|
| Windows | SmartScreen "unrecognized app" dialog | Click "More info" > "Run anyway" |
| macOS | Gatekeeper "cannot be opened" dialog | Right-click > "Open", or: `xattr -cr /Applications/AutoApply.app` |
| Linux | No warning | AppImage is executable by default |

Code signing and notarization are tracked in the [Roadmap](Roadmap) as a future enhancement.

### Cross-Compilation

electron-builder does not reliably support cross-compilation for all platforms. Each platform's installer should be built on that platform (or via CI with the appropriate runner). The CI matrix build handles this automatically.

### Bundle Size

The complete installer is approximately:
- **Windows**: ~250 MB (includes Python + Chromium)
- **macOS**: ~280 MB
- **Linux**: ~260 MB

Most of the size comes from the bundled Chromium browser (~150 MB) and the Python environment (~60 MB).
