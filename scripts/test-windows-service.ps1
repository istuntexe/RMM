#requires -RunAsAdministrator
# Runs only on an isolated Windows CI/test machine; creates/removes the real service.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Set-Location (Split-Path $PSScriptRoot -Parent)
if (Test-Path (Join-Path $env:ProgramData 'HybridRMM')) { throw 'Existing install detected; use an isolated test machine.' }
$Folder = Join-Path ([IO.Path]::GetTempPath()) ('hybridrmm-service-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $Folder | Out-Null
$Listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
$Listener.Start(); $Port = $Listener.LocalEndpoint.Port; $Listener.Stop()
$Base = "http://127.0.0.1:$Port"
$Original = @{}
foreach ($Name in @('RMM_ADMIN_TOKEN','RMM_ENROLLMENT_TOKEN','RMM_PUBLIC_URL')) { $Original[$Name] = [Environment]::GetEnvironmentVariable($Name) }
$env:RMM_ADMIN_TOKEN = (& python -c 'import secrets; print(secrets.token_urlsafe(32))').Trim()
$env:RMM_ENROLLMENT_TOKEN = (& python -c 'import secrets; print(secrets.token_urlsafe(32))').Trim()
$env:RMM_PUBLIC_URL = $Base
$Headers = @{ Authorization = 'Bearer ' + $env:RMM_ADMIN_TOKEN }
$Server = $null
$RecoveryBackup = $null
try {
    $Server = Start-Process python -ArgumentList @('server.py','--port',"$Port",'--database',('"' + (Join-Path $Folder 'db.sqlite') + '"')) -PassThru -RedirectStandardOutput (Join-Path $Folder 'server.log') -RedirectStandardError (Join-Path $Folder 'server-error.log')
    $Ready = $false
    for ($Attempt=0; $Attempt -lt 30; $Attempt++) {
        try { $Health=Invoke-RestMethod "$Base/healthz"; if ($Health.status -eq 'ok') { $Ready=$true; break } } catch { Start-Sleep -Milliseconds 200 }
    }
    if (-not $Ready) { throw 'Test server did not start.' }
    $Archive = Join-Path $Folder 'agent.zip'
    Invoke-WebRequest "$Base/api/agents/windows/download" -Method Post -Headers $Headers -Body '{}' -OutFile $Archive
    $Bundle = Join-Path $Folder 'bundle'
    Expand-Archive $Archive $Bundle
    # Authorize installation only in this isolated fixture; the shipped installer keeps its real prompt.
    function Read-Host { param([string]$Prompt) return 'INSTALL' }
    & (Join-Path $Bundle 'install.ps1')
    Remove-Item Function:\Read-Host
    $Service = Get-Service HybridRMMInventory
    if ($Service.Status -ne 'Running' -or $Service.StartType -ne 'Automatic') { throw 'Service is not running automatically.' }
    $Service.Dispose()
    $Installed = $false
    for ($Attempt=0; $Attempt -lt 30; $Attempt++) {
        $Data = Invoke-RestMethod "$Base/api/devices" -Headers $Headers
        if ($Data.devices.Count -eq 1 -and $Data.devices[0].status -eq 'online' -and $Data.devices[0].inventory.os -eq 'Windows' -and $Data.devices[0].inventory.metrics.memory_total_bytes -gt 0) { $Installed=$true; break }
        Start-Sleep -Milliseconds 300
    }
    if (-not $Installed) { throw 'Installed service did not report Windows device health.' }
    $Before = $Data.devices[0].last_seen
    Restart-Service HybridRMMInventory
    $Restarted = $false
    for ($Attempt=0; $Attempt -lt 30; $Attempt++) {
        $Data = Invoke-RestMethod "$Base/api/devices" -Headers $Headers
        if ($Data.devices[0].last_seen -gt $Before) { $Restarted=$true; break }
        Start-Sleep -Milliseconds 300
    }
    if (-not $Restarted) { throw 'Service did not resume reporting after restart.' }
    & (Join-Path $Bundle 'uninstall.ps1')
    # The uninstaller preserves configuration. Reinstallation must archive, not delete it.
    $InstalledDirectory = Join-Path $env:ProgramData 'HybridRMM'
    $PreviousConfigHash = (Get-FileHash (Join-Path $InstalledDirectory 'device.json')).Hash
    Set-Content (Join-Path $InstalledDirectory 'preserve-test.txt') 'Preserve this local file'
    $ExistingBackups = @(Get-ChildItem $env:ProgramData -Directory -Filter 'HybridRMM-backup-*' | Select-Object -ExpandProperty FullName)
    $SecondArchive = Join-Path $Folder 'second.zip'
    Invoke-WebRequest "$Base/api/agents/windows/download" -Method Post -Headers $Headers -Body '{}' -OutFile $SecondArchive
    $SecondBundle = Join-Path $Folder 'second-bundle'
    Expand-Archive $SecondArchive $SecondBundle
    $SecondConfig = Get-Content (Join-Path $SecondBundle 'device.json') -Raw | ConvertFrom-Json
    function Read-Host { param([string]$Prompt) return 'INSTALL' }
    & (Join-Path $SecondBundle 'install.ps1')
    Remove-Item Function:\Read-Host
    $CreatedBackups = @(Get-ChildItem $env:ProgramData -Directory -Filter 'HybridRMM-backup-*' | Where-Object { $_.FullName -notin $ExistingBackups })
    if ($CreatedBackups.Count -ne 1) { throw 'Recovery must create exactly one preserved backup.' }
    $RecoveryBackup = $CreatedBackups[0].FullName
    if ((Get-FileHash (Join-Path $RecoveryBackup 'device.json')).Hash -ne $PreviousConfigHash) { throw 'Recovery changed the previous credential file.' }
    if ((Get-Content (Join-Path $RecoveryBackup 'preserve-test.txt') -Raw).Trim() -ne 'Preserve this local file') { throw 'Recovery lost local files.' }
    $Recovered = $false
    for ($Attempt=0; $Attempt -lt 30; $Attempt++) {
        $Detail = Invoke-RestMethod ("$Base/api/devices/" + $SecondConfig.id) -Headers $Headers
        if ($Detail.device.status -eq 'online') { $Recovered=$true; break }
        Start-Sleep -Milliseconds 300
    }
    if (-not $Recovered) { throw 'Recovered installation did not report with the new credential.' }
    & (Join-Path $SecondBundle 'uninstall.ps1')
    Write-Host 'Recovery passed: previous credentials and local files preserved; new device reports online.'
    Write-Host 'Windows service test passed: dashboard bundle, installation, LocalService account, automatic startup, real health reporting, restart and uninstall.'
} finally {
    if (Get-Service HybridRMMInventory -ErrorAction SilentlyContinue) {
        Stop-Service HybridRMMInventory -Force -ErrorAction SilentlyContinue
        & sc.exe delete HybridRMMInventory | Out-Null
    }
    if ($Server -and -not $Server.HasExited) { Stop-Process -Id $Server.Id }
    foreach ($Name in $Original.Keys) { [Environment]::SetEnvironmentVariable($Name, $Original[$Name]) }
    if (Test-Path (Join-Path $env:ProgramData 'HybridRMM')) { Remove-Item (Join-Path $env:ProgramData 'HybridRMM') -Recurse -Force }
    if ($RecoveryBackup -and (Test-Path $RecoveryBackup)) { Remove-Item $RecoveryBackup -Recurse -Force }
    Remove-Item $Folder -Recurse -Force
}
