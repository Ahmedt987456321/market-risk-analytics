# Parse powerbi/<model>/definition with the TMDL serializer that ships inside Power BI Desktop,
# and list what it read. A syntax or reference error throws, with the file and line.
#
#   powershell -ExecutionPolicy Bypass -File tools\validate_tmdl.ps1
param(
  [string]$Definition = (Join-Path $PSScriptRoot "..\powerbi\MarketRisk.SemanticModel\definition"),
  [string]$PbiBin = ""
)
if (-not $PbiBin) {
  $pkg = Get-AppxPackage -Name "Microsoft.MicrosoftPowerBIDesktop" | Select-Object -First 1
  $PbiBin = Join-Path $pkg.InstallLocation "bin"
}
[AppDomain]::CurrentDomain.add_AssemblyResolve({ param($s, $e)
  $p = Join-Path $PbiBin ((($e.Name -split ',')[0]) + ".dll"); if (Test-Path $p) { [Reflection.Assembly]::LoadFrom($p) } else { $null } })
[void][Reflection.Assembly]::LoadFrom((Join-Path $PbiBin "Microsoft.PowerBI.Tabular.dll"))

try {
  $db = [Microsoft.AnalysisServices.Tabular.TmdlSerializer]::DeserializeDatabaseFromFolder((Resolve-Path $Definition).Path)
} catch {
  $err = $_.Exception
  while ($err.InnerException) { $err = $err.InnerException }
  Write-Output ("TMDL ERROR: " + $err.GetType().Name + ": " + $err.Message)
  exit 1
}
$m = $db.Model
Write-Output ("database compatibility level {0}, culture {1}" -f $db.CompatibilityLevel, $m.Culture)
foreach ($t in $m.Tables) {
  $src = ($t.Partitions | ForEach-Object { $_.SourceType }) -join ","
  Write-Output ("table {0,-22} columns {1,2}  measures {2,2}  partition {3}" -f $t.Name, $t.Columns.Count, $t.Measures.Count, $src)
}
foreach ($r in $m.Relationships) {
  Write-Output ("relationship {0}[{1}] -> {2}[{3}]" -f $r.FromTable.Name, $r.FromColumn.Name, $r.ToTable.Name, $r.ToColumn.Name)
}
foreach ($e in $m.Expressions) { Write-Output ("expression {0}" -f $e.Name) }
Write-Output "TMDL OK"
