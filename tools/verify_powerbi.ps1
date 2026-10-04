# Read-only check of the report's numbers. Power BI Desktop must be open with powerbi/MarketRisk.pbip
# and refreshed. Connects to Desktop's local analysis engine, runs DAX queries, prints the results.
# Compare them with: python tools/verify_powerbi_expected.py
#
#   powershell -ExecutionPolicy Bypass -File tools\verify_powerbi.ps1
$pkg = Get-AppxPackage -Name "Microsoft.MicrosoftPowerBIDesktop" | Select-Object -First 1
$bin = Join-Path $pkg.InstallLocation "bin"
[void][Reflection.Assembly]::LoadFrom((Join-Path $bin "Microsoft.PowerBI.AdomdClient.dll"))

$engine = Get-Process -Name msmdsrv -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $engine) { Write-Output "Power BI's engine is not running: open powerbi\MarketRisk.pbip first."; exit 1 }
$port = (Get-NetTCPConnection -State Listen -OwningProcess $engine.Id | Where-Object { $_.LocalAddress -in "127.0.0.1", "::1", "0.0.0.0" } |
         Select-Object -First 1).LocalPort
$conn = New-Object Microsoft.AnalysisServices.AdomdClient.AdomdConnection("Data Source=localhost:$port")
$conn.Open()

function Run([string]$title, [string]$dax) {
  Write-Output "== $title"
  $cmd = $conn.CreateCommand(); $cmd.CommandText = $dax
  $r = $cmd.ExecuteReader()
  while ($r.Read()) {
    $vals = for ($i = 0; $i -lt $r.FieldCount; $i++) {
      $v = $r.GetValue($i); if ($v -is [double]) { "{0:N4}" -f $v } else { "$v" }
    }
    Write-Output ("   " + ($vals -join " | "))
  }
  $r.Close()
}

Run "headline" @'
EVALUATE ROW (
  "VaR99", [99% VaR (GBP m)], "ES975", [97.5% ES (GBP m)], "VaR95", [95% VaR (GBP m)],
  "VaR change", [Change in 99% VaR since previous day (GBP m)], "Gross", [Gross exposure (GBP m)],
  "Zone", [Basel zone, last 250 days], "Checks", [Data checks], "Worst stress", [Worst stress scenario] )
'@
Run "backtest, nothing selected" 'EVALUATE ROW ( "Exceptions", [Exceptions (99%)], "Days", [Days tested], "Expected", [Expected exceptions], "Rate", [Exception rate (99%)] )'
Run "backtest by method" 'EVALUATE ADDCOLUMNS ( VALUES ( dim_method[method_label] ), "Exceptions", [Exceptions (99%)], "Rate", [Exception rate (99%)], "Zone", [Basel zone, last 250 days] )'
Run "what moved VaR" 'EVALUATE SUMMARIZECOLUMNS ( fact_var_explain[component_label], "Change", [Change by cause (GBP m)] )'
Run "ES by desk" 'EVALUATE SUMMARIZECOLUMNS ( fact_risk[scope_id], "ES", [ES contribution (GBP m)] )'
Run "stress" 'EVALUATE SUMMARIZECOLUMNS ( dim_scenario[scenario_name], "PnL", [Scenario P&L (GBP m)], "Worst", [Worst point in window (GBP m)] )'
Run "gross by desk" 'EVALUATE SUMMARIZECOLUMNS ( dim_instrument[desk], "Gross", [Gross exposure (GBP m)] )'
Run "top positions" 'EVALUATE TOPN ( 5, SUMMARIZECOLUMNS ( dim_instrument[name], "MV", [Market value today (GBP m)], "Share", [Share of gross exposure] ), [Share], DESC )'
Run "data checks" 'EVALUATE ROW ( "Passed", [Checks passed], "Warnings", [Warnings], "Failures", [Failures] )'
Run "goldman" 'EVALUATE ROW ( "Latest", [Latest quarter, total (USD m)], "Quarters", [Quarters published] )'
Run "row counts" 'EVALUATE { ("positions", COUNTROWS ( fact_position_daily )), ("backtest", COUNTROWS ( fact_backtest )), ("gs_var", COUNTROWS ( fact_gs_var )) }'
$conn.Close()
