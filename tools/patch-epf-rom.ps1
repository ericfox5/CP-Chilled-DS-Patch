# Patch your own dump of Club Penguin: Elite Penguin Force (DS, US) so it connects to CP Chilled.
#   scripts\ds\patch-epf-rom.ps1 "C:\path\to\Club Penguin - Elite Penguin Force.nds"
# Writes "<name> (CP Chilled).nds" next to the dump. Needs Python 3 (https://www.python.org); ndspy is installed on demand.
param([Parameter(Mandatory = $true, Position = 0)][string]$Rom, [string]$Out = "", [string]$Base = "http://ds.cpchilled.com")

$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { Write-Error "Python 3 is not installed (or not on PATH). Install it from https://www.python.org and retry."; exit 1 }
if (-not (Test-Path -LiteralPath $Rom)) { Write-Error "ROM not found: $Rom"; exit 1 }
if (-not $Out) {
  $dir = Split-Path -Parent (Resolve-Path -LiteralPath $Rom)
  $name = [System.IO.Path]::GetFileNameWithoutExtension($Rom)
  $Out = Join-Path $dir "$name (CP Chilled).nds"
}
python -c "import ndspy" 2>$null
if ($LASTEXITCODE -ne 0) { Write-Host "Installing ndspy..."; python -m pip install --user ndspy | Out-Null }
$script = Join-Path $PSScriptRoot "patch-epf-rom.py"
python $script --src $Rom --out $Out --base $Base
if ($LASTEXITCODE -eq 0) { Write-Host "`nDone. Load `"$Out`" in melonDS (Config > Wi-Fi settings: Direct mode OFF) and use Upload/Download in the game." }
