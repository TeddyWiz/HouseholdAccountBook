# 가계부 바로가기 만들기 (make_shortcut.bat 이 실행합니다)
# 바탕화면, 시작 메뉴, 이 폴더에 '가계부' 바로가기를 아이콘과 함께 만듭니다.
$ErrorActionPreference = 'Stop'
$dir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$icon = Join-Path $dir 'assets\app.ico'
$app  = Join-Path $dir 'desktop_app.py'

# pythonw.exe 찾기 (명령 창 없이 실행)
$pyw = $null
try {
    $exe = (& python -c "import sys; print(sys.executable)" 2>$null | Select-Object -First 1)
    if ($exe) {
        $cand = Join-Path (Split-Path -Parent $exe.Trim()) 'pythonw.exe'
        if (Test-Path $cand) { $pyw = $cand }
    }
} catch {}
if (-not $pyw) {
    $cmd = Get-Command pythonw.exe -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($cmd) { $pyw = $cmd.Source }
}

$shell = New-Object -ComObject WScript.Shell
function Make-Shortcut([string]$path) {
    $s = $shell.CreateShortcut($path)
    if ($pyw) {
        $s.TargetPath = $pyw
        $s.Arguments  = '"' + $app + '"'
    } else {
        $s.TargetPath  = Join-Path $dir 'run_app.bat'
        $s.WindowStyle = 7   # 최소화
    }
    $s.WorkingDirectory = $dir
    $s.IconLocation     = "$icon,0"
    $s.Description      = '가계부 - 통장/카드 지출과 수입 관리'
    $s.Save()
    Write-Host "  만듦: $path"
}

Write-Host '가계부 바로가기를 만듭니다...'
Make-Shortcut (Join-Path ([Environment]::GetFolderPath('Desktop'))  '가계부.lnk')
Make-Shortcut (Join-Path ([Environment]::GetFolderPath('Programs')) '가계부.lnk')
Make-Shortcut (Join-Path $dir '가계부.lnk')
if ($pyw) { Write-Host "실행 파일: $pyw" } else { Write-Host 'pythonw.exe 를 찾지 못해 run_app.bat 로 연결했습니다.' }
Write-Host '완료. 바탕화면의 [가계부] 아이콘을 더블클릭하세요.'
