$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Set-Location (Split-Path $PSScriptRoot -Parent)
if ($env:OS -ne 'Windows_NT') { throw 'Build this Windows executable on Windows.' }
python -m venv .venv-build
if ($LASTEXITCODE -ne 0) { throw 'Could not create build environment.' }
$Python = Join-Path $PWD '.venv-build\Scripts\python.exe'
& $Python -m pip install --disable-pip-version-check 'pyinstaller==6.16.0' -r requirements-signaling.txt
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller installation failed.' }
& $Python -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw 'Integration tests failed.' }
& $Python -m PyInstaller --noconfirm --clean --onefile --console --name hybridrmm-agent --distpath dist --workpath build --specpath build agent.py
if ($LASTEXITCODE -ne 0) { throw 'Executable build failed.' }
& $Python scripts/smoke-agent.py dist/hybridrmm-agent.exe
if ($LASTEXITCODE -ne 0) { throw 'Packaged agent failed its functional smoke test.' }
$Executable = Join-Path $PWD 'dist\hybridrmm-agent.exe'
$Hash = (Get-FileHash -Algorithm SHA256 $Executable).Hash.ToLowerInvariant()
[IO.File]::WriteAllText((Join-Path $PWD 'dist\SHA256SUMS.txt'), "$Hash  hybridrmm-agent.exe`n", [Text.UTF8Encoding]::new($false))
$Metadata = @{
    agent_version = '0.1.0'
    git_commit = $env:GITHUB_SHA
    python = (& $Python --version | Out-String).Trim()
    pyinstaller = '6.16.0'
    platform = 'windows-x64'
    authenticode_signed = $false
    sha256 = $Hash
}
$Metadata | ConvertTo-Json | Set-Content -Encoding utf8 dist/build-info.json
Write-Host 'Built and tested dist\hybridrmm-agent.exe (unsigned).'
