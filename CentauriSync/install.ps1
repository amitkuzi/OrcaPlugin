<# CentauriSync local-upgrade aid. It never installs code or changes permissions.
   Upgrade: Backup, use OrcaSlicer File > Plugins > Install local plugin, close Orca,
   Restore, restart Orca. Do not run this tool during a print or while Orca is open. #>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('Backup','Restore')][string]$Action,
    [Parameter(Mandatory)][string]$BackupDir,
    [string]$DataDir = (Join-Path $env:APPDATA 'OrcaSlicer'),
    [ValidateSet('Auto','Native','Legacy')][string]$SourceLayout = 'Auto'
)
$ErrorActionPreference = 'Stop'
$files = @('settings.json','filament_map.json')

function Assert-Regular($Path, [switch]$Directory) {
    $item = Get-Item -LiteralPath $Path -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
        ($item.PSIsContainer -ne [bool]$Directory)) {
        throw "Not a regular $(if ($Directory) {'directory'} else {'file'}): $Path"
    }
}

if (Get-Process -Name 'orca-slicer' -ErrorAction SilentlyContinue) {
    throw 'Close OrcaSlicer before backing up/restoring plugin settings.'
}
$native = Join-Path $DataDir 'orca_plugins\centauri_canvas_sync.py'
$legacy = Join-Path $DataDir 'orca_plugins\centauri_canvas_sync'
$entry = 'centauri_canvas_sync.py'
if ($Action -eq 'Backup') {
    $n = Test-Path -LiteralPath (Join-Path $native $entry) -PathType Leaf
    $l = Test-Path -LiteralPath (Join-Path $legacy $entry) -PathType Leaf
    if ($SourceLayout -eq 'Auto' -and $n -and $l) {
        throw 'Both native and older manual layouts exist. Choose -SourceLayout Native or Legacy after checking which contains your settings.'
    }
    $origin = if ($SourceLayout -eq 'Legacy' -or ($SourceLayout -eq 'Auto' -and $l)) {$legacy} else {$native}
    Assert-Regular $origin -Directory
    Assert-Regular (Join-Path $origin $entry)
    if (Test-Path -LiteralPath $BackupDir) { throw 'Use a new backup directory, outside OrcaSlicer data.' }
    $absBackup = [IO.Path]::GetFullPath($BackupDir)
    $absData = [IO.Path]::GetFullPath($DataDir).TrimEnd([char[]]'\/')
    if ($absBackup.TrimEnd([char[]]'\/').Equals($absData, 'OrdinalIgnoreCase') -or $absBackup.StartsWith($absData + [IO.Path]::DirectorySeparatorChar, 'OrdinalIgnoreCase')) {
        throw 'Backup directory must be outside OrcaSlicer data.'
    }
    foreach ($name in $files) {
        $f=Join-Path $origin $name
        if (Test-Path -LiteralPath $f) { Assert-Regular $f }
    }
    New-Item -ItemType Directory -Path $BackupDir | Out-Null
    foreach ($name in $files) {
        $f=Join-Path $origin $name
        if (Test-Path -LiteralPath $f) { Copy-Item -LiteralPath $f -Destination (Join-Path $BackupDir $name) }
    }
    Write-Host 'Settings backup complete. Use OrcaSlicer to install the new centauri_canvas_sync.py (keep filename).'
} else {
    Assert-Regular $native -Directory
    Assert-Regular (Join-Path $native $entry)
    Assert-Regular $BackupDir -Directory
    foreach ($name in $files) {
        $f=Join-Path $BackupDir $name
        $dest=Join-Path $native $name
        if (!(Test-Path -LiteralPath $f)) {continue}
        Assert-Regular $f
        if (Test-Path -LiteralPath $dest) {
            Assert-Regular $dest
            if ((Get-FileHash -LiteralPath $f).Hash -ne (Get-FileHash -LiteralPath $dest).Hash) {
                throw "Restore would overwrite differing settings: $name. Resolve manually without losing your backup."
            }
        }
    }
    foreach ($name in $files) {
        $f=Join-Path $BackupDir $name
        $dest=Join-Path $native $name
        if ((Test-Path -LiteralPath $f) -and !(Test-Path -LiteralPath $dest)) {
            [IO.File]::Copy((Get-Item -LiteralPath $f).FullName,[IO.Path]::GetFullPath($dest),$false)
        }
    }
    Write-Host 'Saved settings restored. Restart OrcaSlicer and review any new permission prompts.'
}
