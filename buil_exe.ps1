param(
    [ValidateSet("onefile", "onedir")]
    [string]$Mode = "onedir"
)

$ErrorActionPreference = "Stop"
$AppName = "OptimizadorCDI-final-v43"

Write-Host "====================================================" -ForegroundColor Cyan
Write-Host "  Empaquetador de Optimizador CDI (Modo: $Mode)" -ForegroundColor Cyan
Write-Host "====================================================" -ForegroundColor Cyan

# 1. Activar entorno virtual si existe
if (Test-Path ".\.venv\Scripts\Activate.ps1") {
    Write-Host "[1/4] Activando entorno virtual .venv..." -ForegroundColor Yellow
    . .\.venv\Scripts\Activate.ps1
} else {
    Write-Host "[1/4] Usando intérprete de Python global..." -ForegroundColor Yellow
}

# 2. Ejecutar suite completa de pruebas unitarias antes de compilar
Write-Host "[2/4] Ejecutando pruebas unitarias (unittest)..." -ForegroundColor Yellow
python -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Las pruebas fallaron. Se cancela la generación del ejecutable." -ForegroundColor Red
    exit $LASTEXITCODE
}

# 3. Limpiar build/ y solo la salida previa de esta versión dentro de dist/
Write-Host "[3/4] Limpiando build/ y salida previa de $AppName..." -ForegroundColor Yellow
if (Test-Path ".\build") { Remove-Item -Recurse -Force ".\build" }

$targetsToClean = @(
    ".\dist\$AppName.exe",
    ".\dist\$AppName"
)
foreach ($target in $targetsToClean) {
    if (Test-Path $target) {
        try {
            Remove-Item -Recurse -Force $target
        } catch {
            Write-Host "ERROR: No se pudo limpiar '$target'. Cierra el programa si está abierto o sal de esa carpeta en el Explorador." -ForegroundColor Red
            exit 1
        }
    }
}

# 4. Compilar con PyInstaller usando OptimizadorCDI.spec
Write-Host "[4/4] Generando ejecutable con PyInstaller ($Mode)..." -ForegroundColor Yellow
$env:CDI_BUILD_MODE = $Mode
try {
    python -m PyInstaller --noconfirm --clean OptimizadorCDI.spec
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: PyInstaller terminó con código $LASTEXITCODE." -ForegroundColor Red
        exit $LASTEXITCODE
    }
} finally {
    Remove-Item Env:\CDI_BUILD_MODE -ErrorAction SilentlyContinue
}

# 5. Verificar artefacto generado y reportar tamaño
if ($Mode -eq "onedir") {
    $outDir = ".\dist\$AppName"
    $outPath = Join-Path $outDir "$AppName.exe"
    if (Test-Path $outPath) {
        $folderBytes = (Get-ChildItem -Recurse -File $outDir | Measure-Object -Property Length -Sum).Sum
        $sizeMB = [math]::Round($folderBytes / 1MB, 2)
        Write-Host ""
        Write-Host "COMPILACION EXITOSA (ONEDIR - arranque rápido):" -ForegroundColor Green
        Write-Host "  Carpeta   : $outDir" -ForegroundColor Green
        Write-Host "  Ejecutable: $outPath" -ForegroundColor Green
        Write-Host "  Tamaño tot: $sizeMB MB" -ForegroundColor Green
    } else {
        Write-Host "No se encontró el ejecutable esperado en $outDir." -ForegroundColor Red
        exit 1
    }
} else {
    $exePath = ".\dist\$AppName.exe"
    if (Test-Path $exePath) {
        $sizeMB = [math]::Round((Get-Item $exePath).Length / 1MB, 2)
        Write-Host ""
        Write-Host "COMPILACION EXITOSA (ONEFILE):" -ForegroundColor Green
        Write-Host "  Archivo: $exePath" -ForegroundColor Green
        Write-Host "  Tamaño : $sizeMB MB" -ForegroundColor Green
    } else {
        Write-Host "No se encontró el ejecutable esperado en dist/." -ForegroundColor Red
        exit 1
    }
}