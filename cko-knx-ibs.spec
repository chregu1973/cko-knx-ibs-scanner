from pathlib import Path

root = Path(SPEC).resolve().parent

a = Analysis(
    [str(root / "launcher.py")],
    pathex=[str(root)],
    binaries=[],
    datas=[
        (str(root / "cko_ibs" / "static"), "cko_ibs/static"),
        (str(root / "assets"), "assets"),
    ],
    hiddenimports=[
        "cko_ibs.main",
        "uvicorn.logging",
        "uvicorn.loops.auto",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan.on",
        "xknxproject",
        "hid",
        "webview",
        "webview.platforms.edgechromium",
        "clr",
    ],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CKO-KNX-IBS",
    console=False,
    icon=str(root / "assets" / "cko-toolbox.ico"),
    version=str(root / "installer" / "version-info.txt"),
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=True, name="CKO-KNX-IBS")
