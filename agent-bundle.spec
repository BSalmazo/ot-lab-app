# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for OT Lab Agent

Bundles the agent with all dependencies into a standalone executable
"""

import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

# Resolve project root robustly:
# - In some PyInstaller execution contexts, __file__ is not defined in spec scope.
# - Fallback to current working directory, which our CI invokes at repository root.
_spec_path = globals().get("__file__")
if _spec_path:
    root_dir = Path(_spec_path).resolve().parent
else:
    root_dir = Path(os.getcwd()).resolve()

# Data files to include
datas = []
config_file = root_dir / "agent-config.json"
if config_file.exists():
    datas.append((str(config_file), "."))

# Ensure TLS CA bundle is included for requests/certifi in frozen builds.
datas += collect_data_files("certifi")

a = Analysis(
    # Use top-level agent.py as entrypoint so package imports resolve correctly
    [str(root_dir / "agent.py")],
    pathex=[str(root_dir)],
    binaries=[],
    datas=datas,
    hiddenimports=[
        'scapy',
        'scapy.all',
        'scapy.layers',
        'scapy.layers.inet',
        'scapy.layers.inet6',
        'scapy.layers.l2',
        'requests',
        'urllib3',
        'certifi',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludedimports=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="otlab_agent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
