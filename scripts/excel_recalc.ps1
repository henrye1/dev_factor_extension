# Opens a formula workbook in Excel, forces a full recalculation and dumps key ranges to JSON.
# Used to check that the exported formulas reproduce the engine's numbers.
#   pwsh scripts/excel_recalc.ps1 -Path <workbook.xlsx> -Out <result.json> -N <TermSteps> -T <Target>
param(
    [Parameter(Mandatory)] [string] $Path,
    [Parameter(Mandatory)] [string] $Out,
    [Parameter(Mandatory)] [int] $N,
    [Parameter(Mandatory)] [int] $T
)

$excel = New-Object -ComObject Excel.Application
$excel.Visible = $false
$excel.DisplayAlerts = $false
try {
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $wb = $excel.Workbooks.Open($Path, 0, $true)       # no link update, read-only

    # Excel rejects calls while it is still busy with the recalculation it starts on load.
    function Invoke-WhenReady([scriptblock] $action) {
        for ($i = 0; $i -lt 900; $i++) {
            try { return & $action } catch {
                if ($_.Exception.Message -notmatch 'rejected|busy|0x8001010A|0x80010001') { throw }
                Start-Sleep -Seconds 2
            }
        }
        throw 'Excel stayed busy for 30 minutes'
    }
    Invoke-WhenReady { $excel.CalculateFull() }
    Invoke-WhenReady { while ($excel.CalculationState -ne 0) { Start-Sleep -Seconds 1 } }
    $sw.Stop()

    function Column([object] $sheet, [string] $address) {
        $vals = $sheet.Range($address).Value2
        $list = @()
        foreach ($v in $vals) { $list += , $v }
        return $list
    }

    $res = $wb.Worksheets.Item('Results')
    $lgd = $wb.Worksheets.Item('LGD_300')
    $cfg = $wb.Worksheets.Item('Config')
    $idx = $wb.Worksheets.Item('Raw_Index')

    # count error cells on the calculation sheets
    $errors = @{}
    foreach ($name in 'Config', 'Results', 'LGD_300', 'Hazard_Obs', 'Tail_Fit', 'Ext_Exp', 'Ext_Power', 'Ext_LogN', 'Raw_Index') {
        $ws = $wb.Worksheets.Item($name)
        $count = 0
        try { $count = $ws.UsedRange.SpecialCells(-4123, 16).Count } catch { $count = 0 }   # formulas that are errors
        $errors[$name] = $count
    }

    $result = [ordered]@{
        seconds   = [math]::Round($sw.Elapsed.TotalSeconds, 1)
        sel       = Column $res ("I10:I{0}" -f (9 + $N))
        tie       = Column $res ("E10:E{0}" -f (9 + $N))
        final     = Column $lgd ("J13:J{0}" -f (12 + $T))
        lam       = $cfg.Range('B25').Value2
        gam       = $cfg.Range('B26').Value2
        mu        = $cfg.Range('B33').Value2
        sigma     = $cfg.Range('B34').Value2
        lgd_logn  = Column $res ("H10:H{0}" -f (9 + $N))
        avg_sel   = $res.Range('G6').Value2
        tie_max   = $res.Range('B7').Value2
        tie_min   = $res.Range('B8').Value2
        order     = Column $idx ("D5:D{0}" -f (4 + $N))
        errors    = $errors
    }
    $result | ConvertTo-Json -Depth 4 | Set-Content -Path $Out -Encoding UTF8
    $wb.Close($false)
}
finally {
    $excel.Quit()
    [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($excel)
}
