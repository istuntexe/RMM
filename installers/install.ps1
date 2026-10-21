#requires -RunAsAdministrator
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$Source = $PSScriptRoot
$Destination = Join-Path $env:ProgramData 'HybridRMM'
$ServiceName = 'HybridRMMInventory'
Write-Host 'Hybrid RMM Inventory Agent installation'
Write-Host 'Reports hostname, OS, CPU, RAM, system disk usage and uptime every minute.'
Write-Host 'Runs visibly in Windows Services as Hybrid RMM Inventory Agent. No remote commands or screen capture.'
if ((Read-Host 'Type INSTALL to authorize this installation') -cne 'INSTALL') { throw 'Installation cancelled.' }
if (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) { throw 'Hybrid RMM is already installed. Preserve its state; uninstall before replacing it.' }
if (Test-Path $Destination) { throw 'Existing HybridRMM directory found. Preserve its files before a new installation.' }
$Config = Get-Content (Join-Path $Source 'device.json') -Raw | ConvertFrom-Json
$Server = [Uri]$Config.server
if ($Server.Scheme -ne 'https' -and -not ($Server.Scheme -eq 'http' -and $Server.IsLoopback)) { throw 'HTTPS server required.' }
$Expected = ((Get-Content (Join-Path $Source 'SHA256SUMS.txt') | Where-Object { $_ -match '  hybridrmm-agent.exe$' }) -split '\s+')[0]
$Actual = (Get-FileHash (Join-Path $Source 'hybridrmm-agent.exe') -Algorithm SHA256).Hash.ToLowerInvariant()
if ($Actual -ne $Expected) { throw 'Executable checksum does not match the bundle manifest.' }
New-Item -ItemType Directory -Path $Destination | Out-Null
# Restrict credentials to SYSTEM, Administrators and the LocalService account.
& icacls.exe $Destination '/inheritance:r' '/grant:r' '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-19:(OI)(CI)RX' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Could not protect the agent directory.' }
Copy-Item (Join-Path $Source 'hybridrmm-agent.exe') $Destination
Copy-Item (Join-Path $Source 'device.json') $Destination
Copy-Item (Join-Path $Source 'uninstall.ps1') $Destination
$Command = '"' + (Join-Path $Destination 'hybridrmm-agent.exe') + '" --state "' + (Join-Path $Destination 'device.json') + '" --consent --service'
$Credential = [System.Management.Automation.PSCredential]::new('NT AUTHORITY\LocalService', [System.Security.SecureString]::new())
New-Service -Name $ServiceName -DisplayName 'Hybrid RMM Inventory Agent' -Description 'Authorized device inventory and health reporting to your Hybrid RMM server.' -BinaryPathName $Command -StartupType Automatic -Credential $Credential | Out-Null
Start-Service -Name $ServiceName
Write-Host 'Installed. The device should appear online in your admin dashboard within a minute.'
Write-Host ('Uninstall with: powershell -File "' + (Join-Path $Destination 'uninstall.ps1') + '"')
Write-Host 'Protect or delete the downloaded bundle: it contains this device’s credential.'
