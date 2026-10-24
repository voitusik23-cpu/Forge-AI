<#
.SYNOPSIS
    Creates (or repairs) the "Forge AI" shortcut on the Desktop.

.DESCRIPTION
    One double-click has to open the Forge Command Center, so the shortcut points at the virtual
    environment's windowless interpreter and at the launcher, never at the servers:

        target : <repo>\.venv\Scripts\pythonw.exe
        args   : -B -X utf8 tools\forge_launch.py
        workdir: <repo>
        icon   : <repo>\assets\forge.ico

    The launcher itself reuses an already running instance and brings its window forward, so the
    shortcut never starts a second application. Running this script again simply rewrites the same
    properties -- which is also how the icon is refreshed after tools/make_icon.py redraws it.

    The icon location is verified, not assumed. Windows sometimes refuses to render a shortcut icon
    whose file sits inside a folder with a hardened security descriptor (this checkout has one: the
    agent sandbox added explicit ACEs to the repository root). The shell then silently falls back to
    the generic page icon. The script extracts the icon the shortcut would draw and compares it with
    the icon file itself; only if that fails does it retry with the extended-length spelling
    (\\?\C:\...), which bypasses the resolution step that breaks. Whichever form resolves is kept,
    so a healthy machine ends up with the plain path.

    The script writes nothing outside the Desktop folder and never touches other people's files: it
    overwrites its own link only, and deletes nothing.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File tools\make_shortcut.ps1

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File tools\make_shortcut.ps1 -Name 'Forge AI (dev)'
#>
[CmdletBinding()]
param(
    [string]$Name = 'Forge AI',
    [string]$IconPath = ''
)

$ErrorActionPreference = 'Stop'

$repo = Split-Path -Parent $PSScriptRoot
$pythonw = Join-Path $repo '.venv\Scripts\pythonw.exe'
$launcher = Join-Path $repo 'tools\forge_launch.py'
if (-not $IconPath) { $IconPath = Join-Path $repo 'assets\forge.ico' }
$arguments = '-B -X utf8 tools\forge_launch.py'
$description = 'Forge AI ' + [string][char]0x2014 + ' Command Center'

foreach ($required in @($pythonw, $launcher, $IconPath)) {
    if (-not (Test-Path -LiteralPath $required)) {
        throw "Needed file is missing: $required"
    }
}

Add-Type -AssemblyName System.Drawing
if (-not ('ForgeIconCheck' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class ForgeIconCheck {
    [StructLayout(LayoutKind.Sequential)] public struct SIZE { public int cx; public int cy; }
    [ComImport, Guid("bcc18b79-ba16-442f-80c4-8a59c30c463b"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IShellItemImageFactory { [PreserveSig] int GetImage(SIZE size, int flags, out IntPtr phbm); }
    [DllImport("shell32.dll", CharSet = CharSet.Unicode, PreserveSig = false)]
    static extern void SHCreateItemFromParsingName(string path, IntPtr pbc, ref Guid riid,
        [MarshalAs(UnmanagedType.Interface)] out object item);
    [DllImport("gdi32.dll")] public static extern bool DeleteObject(IntPtr handle);

    // The very image Explorer would draw for this item: same shell pipeline, SIIGBF_ICONONLY.
    public static IntPtr Grab(string path, int size, out int hr) {
        hr = 0;
        Guid iid = new Guid("bcc18b79-ba16-442f-80c4-8a59c30c463b");
        object item;
        try { SHCreateItemFromParsingName(path, IntPtr.Zero, ref iid, out item); }
        catch (Exception) { return IntPtr.Zero; }
        IShellItemImageFactory factory = (IShellItemImageFactory)item;
        SIZE wanted = new SIZE();
        wanted.cx = size;
        wanted.cy = size;
        IntPtr bitmap;
        hr = factory.GetImage(wanted, 0x4, out bitmap);
        return hr == 0 ? bitmap : IntPtr.Zero;
    }
}
'@
}

# Two samples that land on the anvil in the drawn icon: the waist and the lower body.
function Get-IconSamples {
    param([string]$Path)
    $hr = 0
    $bitmap = [ForgeIconCheck]::Grab($Path, 32, [ref]$hr)
    if ($bitmap -eq [IntPtr]::Zero) { return $null }
    $image = $null
    try {
        $image = [System.Drawing.Bitmap]::FromHbitmap($bitmap)
        if ($image.Width -ne 32) { return $null }
        $samples = @()
        foreach ($point in @(@(16, 19), @(13, 23))) {
            $colour = $image.GetPixel($point[0], $point[1])
            $samples += ($colour.ToArgb() -band 0xFFFFFF)
        }
        return $samples
    } catch {
        return $null
    } finally {
        if ($image) { $image.Dispose() }
        [ForgeIconCheck]::DeleteObject($bitmap) | Out-Null
    }
}

function Set-ForgeShortcut {
    param([string]$Location, [string]$Link)
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($Link)
    $shortcut.TargetPath = $pythonw
    $shortcut.Arguments = $arguments
    $shortcut.WorkingDirectory = $repo
    $shortcut.IconLocation = $Location
    $shortcut.Description = $description
    $shortcut.WindowStyle = 1
    $shortcut.Save()
}

function Test-IconRendered {
    param([string]$Link)
    $drawn = Get-IconSamples -Path $Link
    $expected = Get-IconSamples -Path $IconPath
    if ($null -eq $drawn -or $null -eq $expected) { return $false }
    for ($i = 0; $i -lt $drawn.Count; $i++) {
        $got = $drawn[$i]
        $want = $expected[$i]
        # Off by a hair is fine (the shell may scale a different frame); a totally different picture
        # -- the generic page icon it falls back to -- is not.
        for ($channel = 0; $channel -lt 3; $channel++) {
            $shift = 8 * $channel
            $delta = [math]::Abs((($got -shr $shift) -band 255) - (($want -shr $shift) -band 255))
            if ($delta -gt 40) { return $false }
        }
    }
    return $true
}

$desktop = [Environment]::GetFolderPath('Desktop')
$link = Join-Path $desktop ($Name + '.lnk')

$iconLocation = $IconPath + ',0'
Set-ForgeShortcut -Location $iconLocation -Link $link
if (-not (Test-IconRendered -Link $link)) {
    Write-Warning ("The shell did not render the icon from '{0}'; retrying with the extended-length form." -f $IconPath)
    $iconLocation = '\\?\' + $IconPath + ',0'
    Set-ForgeShortcut -Location $iconLocation -Link $link
    if (-not (Test-IconRendered -Link $link)) {
        Write-Warning 'The shell still does not render the icon; the shortcut itself is intact.'
    }
}

# A fresh timestamp is what makes Explorer re-read the icon instead of its cached copy.
(Get-Item -LiteralPath $link).LastWriteTime = Get-Date
$cacheTool = Join-Path $env:SystemRoot 'System32\ie4uinit.exe'
if (Test-Path -LiteralPath $cacheTool) {
    & $cacheTool -show 2>$null | Out-Null
}

$check = (New-Object -ComObject WScript.Shell).CreateShortcut($link)
$report = [ordered]@{
    'shortcut'         = $link
    'target'           = $check.TargetPath
    'arguments'        = $check.Arguments
    'workingDirectory' = $check.WorkingDirectory
    'icon'             = $check.IconLocation
    'description'      = $check.Description
    'windowStyle'      = $check.WindowStyle
    'targetExists'     = Test-Path -LiteralPath $check.TargetPath
    'iconExists'       = Test-Path -LiteralPath ($check.IconLocation -replace '^\\\\\?\\', '' -replace ',\d+$', '')
    'iconRendered'     = Test-IconRendered -Link $link
}
$report.GetEnumerator() | ForEach-Object { '{0,-17}: {1}' -f $_.Key, $_.Value }
