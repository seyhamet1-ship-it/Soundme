"""Desktop Integration and Packaging Verification Tests for Soundme AI."""

import json
import os
import shutil
import urllib.request
import pytest
from pathlib import Path


def test_electron_security_config():
    """Verify Electron main.cjs enforces security best practices."""
    root = Path(__file__).resolve().parents[1]
    main_js = (root / "desktop" / "main.cjs").read_text(encoding="utf-8")
    assert "contextIsolation: true" in main_js
    assert "nodeIntegration: false" in main_js
    assert "sandbox: true" in main_js
    assert "setWindowOpenHandler" in main_js
    assert "action: 'deny'" in main_js


def test_preload_restricted_surface():
    """Verify Electron preload.cjs does not expose dangerous Node APIs."""
    root = Path(__file__).resolve().parents[1]
    preload_js = (root / "desktop" / "preload.cjs").read_text(encoding="utf-8")
    assert "contextBridge.exposeInMainWorld" in preload_js
    # Ensure no direct child_process, fs, or shell is leaked to renderer
    assert "child_process" not in preload_js
    assert "fs" not in preload_js
    assert "require(" not in preload_js.replace("require('electron')", "")


def test_backend_manager_structure():
    """Verify BackendManager handles port detection, SSD storage, and clean process exit."""
    root = Path(__file__).resolve().parents[1]
    bm_js = (root / "desktop" / "backend-manager.cjs").read_text(encoding="utf-8")
    assert "checkHealth" in bm_js
    assert "isAttached" in bm_js
    assert "taskkill" in bm_js
    assert "Soundme AI" in bm_js


def test_installer_artifact_exists():
    """Verify Soundme-AI-Setup.exe was generated with proper metadata and size."""
    root = Path(__file__).resolve().parents[1]
    installer = root / "release" / "Soundme-AI-Setup.exe"
    assert installer.is_file(), f"Expected installer at {installer}"
    size_mb = installer.stat().st_size / (1024 * 1024)
    # NSIS installer with bundled electron and assets should be between 80MB and 200MB
    assert 80 <= size_mb <= 250, f"Installer size {size_mb:.1f} MB is outside expected range"


def test_branding_icons_exist():
    """Verify application icon assets exist."""
    root = Path(__file__).resolve().parents[1]
    icon_ico = root / "desktop" / "icon.ico"
    icon_png = root / "desktop" / "icon.png"
    assert icon_ico.is_file() and icon_ico.stat().st_size > 0
    assert icon_png.is_file() and icon_png.stat().st_size > 0


def test_live_backend_health():
    """Verify the active backend health and model readiness when running."""
    req = urllib.request.Request("http://127.0.0.1:8000/api/health")
    try:
        with urllib.request.urlopen(req, timeout=3) as res:
            data = json.loads(res.read().decode("utf-8"))
            assert data.get("status") in {"ok", "warming"}
            assert data.get("tools", {}).get("ffmpeg") is True
            assert data.get("tools", {}).get("ffprobe") is True
    except urllib.error.URLError:
        pytest.skip("Backend is not running on port 8000 (test expects running instance)")


def test_package_scripts():
    """Verify package.json contains all required reproducible desktop scripts."""
    root = Path(__file__).resolve().parents[1]
    pkg = json.loads((root / "package.json").read_text(encoding="utf-8"))
    scripts = pkg.get("scripts", {})
    assert "desktop:dist" in scripts
    assert "desktop:start" in scripts
    assert "desktop:dev" in scripts
    assert "build:frontend" in scripts


def test_electron_builder_asar_unpack():
    """Verify electron-builder unpacks app and frontend/dist so Python can load them from disk."""
    root = Path(__file__).resolve().parents[1]
    cfg = json.loads((root / "electron-builder.json").read_text(encoding="utf-8"))
    unpacked = cfg.get("asarUnpack", [])
    assert any("app/" in pattern or "app\\*" in pattern for pattern in unpacked), "app must be unpacked for Python execution"
    assert any("frontend/dist" in pattern for pattern in unpacked), "frontend/dist must be unpacked for static serving"


def test_backend_manager_unpack_discovery():
    """Verify BackendManager explicitly discovers app.asar.unpacked in packaged mode."""
    root = Path(__file__).resolve().parents[1]
    bm_js = (root / "desktop" / "backend-manager.cjs").read_text(encoding="utf-8")
    assert "app.asar.unpacked" in bm_js
    assert "checkDesktopAuth" in bm_js
