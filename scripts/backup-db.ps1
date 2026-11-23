param(
  [string]$SourceDb = "C:\\opt\\consultiq\\app\\data\\consultiq.db",
  [string]$BackupDir = "C:\\opt\\consultiq\\backups"
)

New-Item -ItemType Directory -Path $BackupDir -Force | Out-Null
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$dest = Join-Path $BackupDir "consultiq_$stamp.db"
Copy-Item -LiteralPath $SourceDb -Destination $dest -Force
Write-Output "Backup created: $dest"
