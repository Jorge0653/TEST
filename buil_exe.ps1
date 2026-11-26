# STREAMING_CHUNK:Configuring PowerShell build script with pre-build test verification...
$ErrorActionPreference = "Stop"

Write-Host "====================================================" -ForegroundColor Cyan
Write-Host "  Empaquetador de Optimizador CDI (Windows .exe)" -ForegroundColor Cyan
Write-Host "====================================================" -ForegroundColor Cyan

# 1. Activar entorno virtual si existe
if (Test-Path ".\.venv\Scripts\Activate.ps1") {
    Write-Host "[1/4] Activando entorno virtual .venv..." -ForegroundColor Yellow
    . .\.venv\Scripts\Activate.ps1
} else {
    Write-Host "[1/4] Usando intérprete de Python global..." -ForegroundColor Yellow
}

# 2. Ejecutar suite de pruebas unitarias antes de compilar
Write-Host "[2/4] Ejecutando pruebas unitarias (unittest)..." -ForegroundColor Yellow
python -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Las pruebas fallaron. Se cancela la generación del .exe." -ForegroundColor Red
    exit $LASTEXITCODE
}

# 3. Limpiar compilaciones previas
Write-Host "[3/4] Limpiando carpetas temporales build/ y dist/..." -ForegroundColor Yellow
if (Test-Path ".\build") { Remove-Item -Recurse -Force ".\build" }

# STREAMING_CHUNK:Running PyInstaller and reporting final executable size...
# 4. Compilar con PyInstaller usando el archivo OptimizadorCDI.spec optimizado
Write-Host "[4/4] Generando ejecutable independiente con PyInstaller..." -ForegroundColor Yellow
python -m PyInstaller --noconfirm --clean OptimizadorCDI.spec

$exePath = ".\dist\OptimizadorCDI-final-v42.exe"
if (Test-Path $exePath) {
    $sizeMB = [math]::Round((Get-Item $exePath).Length / 1MB, 2)
    Write-Host ""
    Write-Host "COMPILACION EXITOSA:" -ForegroundColor Green
    Write-Host "  Archivo: $exePath" -ForegroundColor Green
    Write-Host "  Tamaño : $sizeMB MB" -ForegroundColor Green
} else {
    Write-Host "No se encontró el ejecutable esperado en dist/." -ForegroundColor Red
    exit 1
}