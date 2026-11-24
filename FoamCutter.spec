# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['/Users/michaelmellow/Desktop/droneteam/foamcutter/slicer.pyw'],
    pathex=[],
    binaries=[],
    datas=[('/Users/michaelmellow/Desktop/droneteam/foamcutter/examples', 'examples')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='FoamCutter',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='FoamCutter',
)
app = BUNDLE(
    coll,
    name='FoamCutter.app',
    icon=None,
    bundle_identifier=None,
)
