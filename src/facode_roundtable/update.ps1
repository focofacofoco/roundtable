param(
    [Parameter(Mandatory = $true)][int]$ParentProcessId,
    [Parameter(Mandatory = $true)][string]$UvPath,
    [Parameter(Mandatory = $true)][string]$WheelPath,
    [Parameter(Mandatory = $true)][string]$StagingPath,
    [AllowEmptyString()][string]$ToolPythonPath = "",
    [AllowEmptyString()][string]$RoundtableLauncherPath = ""
)

$resolvedStaging = (Resolve-Path -LiteralPath $StagingPath -ErrorAction Stop).Path
$resolvedWheel = (Resolve-Path -LiteralPath $WheelPath -ErrorAction Stop).Path
if ((Split-Path -Parent $resolvedWheel) -ne $resolvedStaging) {
    exit 3
}
$hasToolPython = -not [string]::IsNullOrWhiteSpace($ToolPythonPath)
$hasLauncher = -not [string]::IsNullOrWhiteSpace($RoundtableLauncherPath)
if ($hasToolPython -ne $hasLauncher) {
    exit 3
}
if ($hasToolPython) {
    $resolvedToolPython = (Resolve-Path -LiteralPath $ToolPythonPath -ErrorAction Stop).Path
    $resolvedLauncher = (Resolve-Path -LiteralPath $RoundtableLauncherPath -ErrorAction Stop).Path
    $scriptsDirectory = Split-Path -Parent $resolvedToolPython
    $toolDirectory = Split-Path -Parent $scriptsDirectory
    if (
        (Split-Path -Leaf $scriptsDirectory) -ine "Scripts" -or
        (Split-Path -Leaf $toolDirectory) -ine "facode-roundtable" -or
        (Split-Path -Leaf $resolvedLauncher) -ine "roundtable.exe"
    ) {
        exit 3
    }
}

$parent = Get-Process -Id $ParentProcessId -ErrorAction SilentlyContinue
if ($null -ne $parent) {
    Wait-Process -Id $ParentProcessId
}

if ($hasToolPython) {
    $commandPattern = '(?i)^"?' + [regex]::Escape($resolvedToolPython) +
        '"?\s+"?' + [regex]::Escape($resolvedLauncher) + '"?(?:\s|$)'
    $processes = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue
    $taskkill = Join-Path $env:SystemRoot "System32\taskkill.exe"
    foreach ($process in $processes) {
        if (
            $process.ExecutablePath -ieq $resolvedToolPython -and
            $process.CommandLine -match $commandPattern
        ) {
            $null = & $taskkill /PID $process.ProcessId /T /F
            if ($LASTEXITCODE -ne 0) {
                exit 3
            }
        }
    }
}

& $UvPath tool install --force $resolvedWheel
$result = $LASTEXITCODE
Remove-Item -LiteralPath $StagingPath -Recurse -Force -ErrorAction SilentlyContinue
exit $result
