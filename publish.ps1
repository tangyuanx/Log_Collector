# Run from this project folder on Windows with Git configured and authorized.
# The GitHub Actions workflow automatically builds the EXE and publishes the
# first release matching update.VERSION after the push to main.
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw 'Git is not installed or not in PATH.' }
if (-not (Test-Path .git)) { git init; if ($LASTEXITCODE -ne 0) { throw 'git init failed' } }
git branch -M main
if ($LASTEXITCODE -ne 0) { throw 'Unable to select main branch' }
$origin = git remote get-url origin 2>$null
if ($LASTEXITCODE -ne 0) {
    git remote add origin https://github.com/tangyuanx/Log_Collector.git
} elseif ($origin -ne 'https://github.com/tangyuanx/Log_Collector.git') {
    throw "Remote origin points to unexpected repository: $origin"
}
python -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw 'Tests failed; not publishing' }
git add --all
if ($LASTEXITCODE -ne 0) { throw 'git add failed' }
git diff --cached --quiet
if ($LASTEXITCODE -ne 0) {
    git commit -m 'feat: initial Log Collector v0.1.0 with Windows CI and updater'
    if ($LASTEXITCODE -ne 0) { throw 'git commit failed (check Git identity)' }
}
git push -u origin main
if ($LASTEXITCODE -ne 0) { throw 'git push failed; check credentials and branch state' }
Write-Host 'Source pushed successfully. Check GitHub Actions for the Windows build and Release.'
Write-Host 'https://github.com/tangyuanx/Log_Collector/actions'
Write-Host 'https://github.com/tangyuanx/Log_Collector/releases'
