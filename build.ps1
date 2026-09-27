$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

python -m PyInstaller --noconfirm --clean --windowed --name LunaSystemCore --icon "$Root\assets\luna.ico" --add-data "$Root\assets;assets" main.py
Write-Host "Built: $Root\dist\LunaSystemCore\LunaSystemCore.exe"
