@echo off
rem Double-click to stop LGD Tail Extension (whatever is serving on port 8000 on this PC).
pwsh -NoProfile -ExecutionPolicy Bypass -Command ^
  "$c = Get-NetTCPConnection -State Listen -LocalPort 8000 -ErrorAction SilentlyContinue | Select-Object -First 1;" ^
  "if (-not $c) { 'The app is not running.'; exit }" ^
  "$p = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $c.OwningProcess);" ^
  "if ($p.CommandLine -notmatch 'hazard_ext') { 'Port 8000 is used by another program (' + $p.Name + '). Nothing was stopped.'; exit }" ^
  "$ids = @($p.ProcessId); $parent = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $p.ParentProcessId);" ^
  "if ($parent -and $parent.CommandLine -match 'hazard_ext') { $ids += $parent.ProcessId }" ^
  "$ids | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }; 'The app has been stopped.'"
ping -n 4 127.0.0.1 >nul
