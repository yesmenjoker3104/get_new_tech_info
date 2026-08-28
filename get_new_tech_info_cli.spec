from PyInstaller.building.build_main import Analysis, PYZ, EXE


analysis = Analysis(
    ["cli_entry.py"],
    pathex=["."],
    binaries=[],
    datas=[],
    hiddenimports=[
        "get_new_tech_info.fetchers.rss",
        "get_new_tech_info.fetchers.hackernews",
        "get_new_tech_info.fetchers.github_trending",
        "get_new_tech_info.fetchers.ogp",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["flet", "flet_desktop"],
    noarchive=False,
)

pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="GetNewTechInfoCli",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
)