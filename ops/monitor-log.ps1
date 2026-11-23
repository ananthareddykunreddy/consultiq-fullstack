param(
  [string]$LogFile = "C:\\opt\\consultiq\\app\\data\\logs\\app.log"
)
if (-Not (Test-Path $LogFile)) {
  Write-Output "Log file not found: $LogFile"
  exit 1
}
$lines = Get-Content -Path $LogFile -Tail 200
$errors = $lines | Select-String -Pattern "ERROR|Exception|429"
Write-Output "Recent lines: $($lines.Count)"
Write-Output "Potential issues: $($errors.Count)"
$errors | Select-Object -First 30 | ForEach-Object { $_.Line }
