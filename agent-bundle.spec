# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for OT Lab Agent

Bundles the agent with all dependencies into a standalone executable
"""

import sys
import os
from pathlib import Path

block_cipher = None

# Determine the root directory - use current working directory
# PyInstaller runs from the project root directory
root_dir = Path(os.getcwd())
agent_dir = root_dir / "agent"

# Data files to include
datas = []
config_file = root_dir / "agent-config.json"
if config_file.exists():
    datas.append((str(config_file), "."))

a = Analysis(
    [str(agent_dir / "main.py")],
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

