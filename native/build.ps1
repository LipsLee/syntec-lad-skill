$ErrorActionPreference = 'Stop'

$csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $csc)) {
    throw "csc.exe not found at $csc"
}

$root = Split-Path -Parent $PSScriptRoot
$source = Join-Path $PSScriptRoot 'LadSyntaxCheck.cs'
$outputDir = Join-Path $PSScriptRoot 'bin'
$output = Join-Path $outputDir 'LadSyntaxCheck.exe'
New-Item -ItemType Directory -Force -Path $outputDir | Out-Null

& $csc /nologo /target:exe /platform:x86 "/out:$output" $source
if ($LASTEXITCODE -ne 0) {
    throw "csc failed with exit code $LASTEXITCODE"
}
Write-Output "built $output"
