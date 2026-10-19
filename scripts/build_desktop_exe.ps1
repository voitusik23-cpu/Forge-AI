<#
.SYNOPSIS
Build the Forge AI desktop launcher as a real Windows executable.

.DESCRIPTION
Compiles tools/forge_desktop_launcher.cs with the C# compiler that ships with Windows. No new
framework is required, and nothing about the backend or the React frontend changes: the executable
locates the repository, verifies prerequisites, and starts the existing tools/forge_launch.py.

The compiler must be invoked by PowerShell directly. Driving it through another interpreter makes the
compiler's output write fail with CS0016 "Access denied" in sandboxed environments; running it here
produces the executable normally.

.PARAMETER Output
Where to place the built executable. Default: dist/Forge AI.exe
#>
[CmdletBinding()]
param(
    [string]$Output = ''
)

$ErrorActionPreference = "Stop"
# $PSScriptRoot is not reliably available inside a param default, so the path is resolved here.
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $Output) { $Output = Join-Path $repo 'dist\Forge AI.exe' }
$source = Join-Path $repo "tools\forge_desktop_launcher.cs"
$icon = Join-Path $repo "assets\forge.ico"
$outputPath = [System.IO.Path]::GetFullPath($Output)

foreach ($required in @($source, $icon)) {
    if (-not (Test-Path $required)) { throw "Missing required file: $required" }
}

$csc = @(
    "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe",
    "$env:WINDIR\Microsoft.NET\Framework\v4.0.30319\csc.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $csc) { throw "No C# compiler found. Install .NET Framework 4.x, or use PyInstaller instead." }

# The compiler writes into a staging directory: in this environment it is refused write
# access to an .exe created directly under dist/, while copying the finished file there works.
$staging = Join-Path $repo ".forge\exe-build"
New-Item -ItemType Directory -Force -Path $staging, (Split-Path $outputPath) | Out-Null
# A unique staging name: a previously launched launcher (or an earlier build) can still hold
# the old file open, and the compiler then fails with CS0016 "Access denied" on an overwrite.
$compiled = Join-Path $staging ("ForgeAI-build-{0}.exe" -f $PID)

# Run the compiler directly from this shell: see .DESCRIPTION.
& $csc /nologo /target:winexe /optimize+ "/out:$compiled" "/win32icon:$icon" `
    /reference:System.Windows.Forms.dll /reference:System.Drawing.dll $source
if ($LASTEXITCODE -ne 0) { throw "Compilation failed with exit code $LASTEXITCODE" }
try {
    Copy-Item -Path $compiled -Destination $outputPath -Force -ErrorAction Stop
} catch {
    Write-Host ("Could not replace {0}: it is in use. Close the running Forge AI window and run this script again." -f $outputPath)
    Remove-Item -Path $compiled -Force -ErrorAction SilentlyContinue
    exit 3
}
Remove-Item -Path $compiled -Force -ErrorAction SilentlyContinue

Write-Host ("Built: {0} ({1} KB)" -f $outputPath, [math]::Round((Get-Item $outputPath).Length / 1KB, 1))
& $outputPath --check
Write-Host ("Prerequisite check exit code: {0}" -f $LASTEXITCODE)
