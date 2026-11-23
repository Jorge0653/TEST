# -*- mode: python ; coding: utf-8 -*-
"""
Archivo .spec optimizado para empaquetar Optimizador CDI en un único .exe de Windows.
Uso:
    python -m PyInstaller --noconfirm --clean OptimizadorCDI.spec
"""

import os

# Excluimos módulos pesados de PySide6 y librerías científicas/GUI que no usa el proyecto
# para reducir drásticamente el peso del .exe y acelerar el tiempo de arranque en frío.
EXCLUDED_MODULES = [
    # Módulos pesados de PySide6 que no se utilizan (solo usamos QtCore, QtGui, QtWidgets)
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebChannel",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuickWidgets",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DExtras",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtNetwork",
    "PySide6.QtPositioning",
    "PySide6.QtBluetooth",
    "PySide6.QtSensors",
    "PySide6.QtSerialPort",
    "PySide6.QtSql",
    "PySide6.QtSvg",
    "PySide6.QtSvgWidgets",
    "PySide6.QtTest",
    "PySide6.QtXml",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets",
    # Módulos de la librería estándar o terceros innecesarios en producción
    "tkinter",
    "unittest",
    "pydoc",
    "pdb",
    "numpy",
    "matplotlib",
    "scipy",
    "PIL",
]

version_file = "version_info.txt" if os.path.isfile("version_info.txt") else None

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("logo.png", "."),
        ("app.ico", "."),
    ],
    hiddenimports=[
        "sqlite3",
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDED_MODULES,
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="OptimizadorCDI-final-v42",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[
        # Evita comprimir con UPX las DLLs críticas de Qt y Python para no causar
        # falsos positivos en antivirus ni retrasos al cargar en memoria
        "vcruntime140.dll",
        "python*.dll",
        "Qt6Core.dll",
        "Qt6Gui.dll",
        "Qt6Widgets.dll",
    ],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=["app.ico"],
    version=version_file,
)