param(
    [Parameter(Mandatory=$false)]
    [string]$RemoteUrl = ""
)

$ErrorActionPreference = "Stop"

Write-Host "=====================================================" -ForegroundColor Cyan
Write-Host " AgriTwin Cloud Data Management Server - GitHub Sync " -ForegroundColor Cyan
Write-Host "=====================================================" -ForegroundColor Cyan

# Locate git.exe even if not in current session PATH
$gitExe = "git"
if (-not (Get-Command $gitExe -ErrorAction SilentlyContinue)) {
    $candidates = @(
        "C:\Program Files\Git\cmd\git.exe",
        "C:\Program Files (x86)\Git\cmd\git.exe",
        "$env:LOCALAPPDATA\Programs\Git\cmd\git.exe"
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) {
            $gitExe = $c
            break
        }
    }
}

if (-not (Get-Command $gitExe -ErrorAction SilentlyContinue) -and -not (Test-Path $gitExe)) {
    Write-Host "[!] Git is not installed yet. Installing Git via winget..." -ForegroundColor Yellow
    winget install --id Git.Git -e --source winget
    $gitExe = "C:\Program Files\Git\cmd\git.exe"
}

if (-not (Test-Path ".git")) {
    & $gitExe init
    & $gitExe branch -M main
}

& $gitExe add .
$status = & $gitExe status --porcelain
if ($status) {
    & $gitExe commit -m "Add Dockerized Cloud Data Server (FastAPI + DynamoDB + RL Advisor + IoT Simulator)"
    Write-Host "[OK] Committed all server & Unity sync files to branch 'main'." -ForegroundColor Green
} else {
    Write-Host "[OK] Working tree is already clean and committed." -ForegroundColor Green
}

if ($RemoteUrl -ne "") {
    $existingRemotes = & $gitExe remote
    if ($existingRemotes -contains "origin") {
        & $gitExe remote set-url origin $RemoteUrl
    } else {
        & $gitExe remote add origin $RemoteUrl
    }
    Write-Host "Pushing to $RemoteUrl ..." -ForegroundColor Cyan
    & $gitExe push -u origin main
    Write-Host "[OK] Successfully pushed to GitHub ($RemoteUrl)!" -ForegroundColor Green
} else {
    Write-Host ""
    Write-Host "Next step to push to your GitHub repository:" -ForegroundColor Yellow
    Write-Host "  .\push_to_github.ps1 -RemoteUrl https://github.com/<your-username>/<your-repo>.git" -ForegroundColor White
}
