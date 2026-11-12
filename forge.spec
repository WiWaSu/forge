block_cipher = None

a = Analysis(
    ["app.py"],
    pathex=["."],
    binaries=[],
    datas=[
        ("knowledge/reference", "knowledge/reference"),
        ("knowledge/examples", "knowledge/examples"),
        ("instructions", "instructions"),
        ("html", "html"),
        ("models", "models"),
    ],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "unittest", "pydoc", "doctest", "test",
              "numpy", "pandas", "matplotlib", "PIL", "setuptools", "pip"],
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
    name="FORGE",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=True,
    icon="forge.ico",
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
