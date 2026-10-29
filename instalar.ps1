# Instalador do Prospecta para Windows
# Uso (PowerShell):  irm https://raw.githubusercontent.com/Gagliardi13/prospecta/main/instalar.ps1 | iex
$ErrorActionPreference = "Stop"
$Repo = "Gagliardi13/prospecta"
$Fonte = "prospecta @ https://github.com/$Repo/archive/refs/heads/main.zip"
$Pasta = Join-Path $env:USERPROFILE "Prospecta"

Write-Host "`n  Instalando o Prospecta...`n" -ForegroundColor Cyan

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "  Instalando o uv (gerenciador de Python)..."
    powershell -NoProfile -ExecutionPolicy ByPass -Command "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

uv tool install --python 3.12 --force "$Fonte"
if ($LASTEXITCODE -ne 0) { throw "Falha ao instalar o Prospecta." }

New-Item -ItemType Directory -Force -Path $Pasta | Out-Null
$Iniciar = Join-Path $Pasta "Iniciar Prospecta.cmd"
@"
@echo off
title Prospecta
set "PATH=%USERPROFILE%\.local\bin;%PATH%"
echo.
echo   Procurando atualizacoes...
uv tool install --quiet --python 3.12 --refresh-package prospecta --reinstall-package prospecta "$Fonte" 2>nul || echo   Sem internet ou GitHub fora do ar: abrindo a versao instalada.
echo   Abrindo o Prospecta (para fechar, feche esta janela)...
prospecta
pause
"@ | Set-Content -Path $Iniciar -Encoding ASCII

$Desktop = [Environment]::GetFolderPath("Desktop")
$Atalho = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $Desktop "Prospecta.lnk"))
$Atalho.TargetPath = $Iniciar
$Atalho.WorkingDirectory = $Pasta
$Atalho.IconLocation = "shell32.dll,22"
$Atalho.Save()

Write-Host "`n  Pronto! Use o atalho 'Prospecta' na sua area de trabalho." -ForegroundColor Green
Write-Host "  Ele se atualiza sozinho toda vez que voce abrir.`n"
Start-Process $Iniciar
