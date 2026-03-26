# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for OT Lab Agent

Bundles the agent with all dependencies into a standalone executable
"""

import sys
from pathlib import Path

block_cipher = None

# Determine the root directory (parent of ot_lab_app)
root_dir = Path(__file__).parent
agent_dir = root_dir / "agent"

a = Analysis(
    [str(agent_dir / "main.py")],
    pathex=[str(root_dir)],
    binaries=[],
    datas=[
        (str(root_dir / "agent-config.json"), ".") if (root_dir / "agent-config.json").exists() else None,
    ],
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

# Remove None entries from datas
a.datas = [x for x in a.datas if x is not None]

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

# For macOS, create a bundle
if sys.platform == "darwin":
    app = BUNDLE(
        exe,
        name="OT Lab Agent.app",
        icon=None,
        bundle_identifier="com.otlab.agent",
        info_plist={
            "NSPrincipalClass": "NSApplication",
            "NSHighResolutionCapable": "True",
        },
    )
