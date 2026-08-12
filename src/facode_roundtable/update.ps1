param(
    [Parameter(Mandatory = $true)][int]$ParentProcessId,
    [Parameter(Mandatory = $true)][string]$UvPath,
    [Parameter(Mandatory = $true)][string]$Source
)

$parent = Get-Process -Id $ParentProcessId -ErrorAction SilentlyContinue
if ($null -ne $parent) {
    Wait-Process -Id $ParentProcessId
}

& $UvPath tool install --force $Source
exit $LASTEXITCODE
