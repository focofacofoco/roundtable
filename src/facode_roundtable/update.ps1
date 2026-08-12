param(
    [Parameter(Mandatory = $true)][int]$ParentProcessId,
    [Parameter(Mandatory = $true)][string]$UvPath,
    [Parameter(Mandatory = $true)][string]$WheelPath,
    [Parameter(Mandatory = $true)][string]$StagingPath
)

$resolvedStaging = (Resolve-Path -LiteralPath $StagingPath -ErrorAction Stop).Path
$resolvedWheel = (Resolve-Path -LiteralPath $WheelPath -ErrorAction Stop).Path
if ((Split-Path -Parent $resolvedWheel) -ne $resolvedStaging) {
    exit 3
}

$parent = Get-Process -Id $ParentProcessId -ErrorAction SilentlyContinue
if ($null -ne $parent) {
    Wait-Process -Id $ParentProcessId
}

& $UvPath tool install --force $resolvedWheel
$result = $LASTEXITCODE
Remove-Item -LiteralPath $StagingPath -Recurse -Force -ErrorAction SilentlyContinue
exit $result
