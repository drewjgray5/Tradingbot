# Lower priority of all running python processes to BelowNormal so foreground
# apps (browser, IDE, etc.) stay responsive while long backtests run.
# Usage: powershell -File schwab_skill\scripts\_lower_python_priority.ps1
# Optionally pass "Idle" as the first arg for the most aggressive throttling.
param([string]$Priority = "BelowNormal")

$valid = @("Idle", "BelowNormal", "Normal", "AboveNormal", "High")
if ($valid -notcontains $Priority) {
    Write-Host "Invalid priority '$Priority'. Must be one of: $($valid -join ', ')"
    exit 1
}

$procs = Get-Process | Where-Object { $_.ProcessName -match "python" }
if ($procs.Count -eq 0) {
    Write-Host "No python processes found."
    exit 0
}
Write-Host "Setting $($procs.Count) python processes to $Priority..."
foreach ($p in $procs) {
    try {
        $p.PriorityClass = $Priority
    } catch {
        Write-Host "  PID $($p.Id): FAILED ($($_.Exception.Message))"
    }
}
Get-Process | Where-Object { $_.ProcessName -match "python" } | Format-Table Id, ProcessName, PriorityClass, @{N='CPU(s)';E={[int]$_.CPU}} -AutoSize
