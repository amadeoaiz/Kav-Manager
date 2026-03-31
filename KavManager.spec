# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec file for KavManager
# Build with: pyinstaller KavManager.spec

import os

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('src/assets', 'src/assets'),
        ('docs', 'docs'),
    ],
    hiddenimports=[
        'sqlalchemy.dialects.sqlite',
        'pulp',
        'pulp.apis',
        'pulp.apis.coin_api',
        'matrix_nio',
        'nio',
        'aiohttp',
        'aiofiles',
        'h11',
        'h2',
        'jsonschema',
        'matplotlib',
        'matplotlib.backends.backend_qtagg',
        'matplotlib.backends.backend_agg',
        'numpy',
        'fpdf',
        'fpdf.enums',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        '_tkinter',
        'matplotlib.tests',
        'numpy.tests',
        'PIL._tkinter_finder',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='KavManager',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon='src/assets/kavmanager.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='KavManager',
)
