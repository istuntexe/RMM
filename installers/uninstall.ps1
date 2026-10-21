#requires -RunAsAdministrator
$ErrorActionPreference = 'Stop'
$Service = Get-Service -Name 'HybridRMMInventory' -ErrorAction SilentlyContinue
if ($Service) {
    Stop-Service -Name 'HybridRMMInventory' -Force
    & sc.exe delete HybridRMMInventory | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not remove the inventory service.' }
}
Write-Host 'Service removed. The device will become offline in the dashboard.'
Write-Host 'Revoke its credential in the dashboard if it will not be reinstalled.'
Write-Host 'Protected configuration remains in C:\ProgramData\HybridRMM. Preserve it or remove that directory after the service is gone.'
