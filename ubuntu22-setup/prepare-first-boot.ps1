param([string]$BootPath)
$ErrorActionPreference = 'Stop'

function Read-SecretText([string]$Prompt) {
    $secret = Read-Host $Prompt -AsSecureString
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
    try { [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
}

function Quote-Yaml([string]$Value) {
    "'" + $Value.Replace("'", "''") + "'"
}

function Check-Wifi([string]$Ssid, [string]$Password) {
    $length = [Text.Encoding]::UTF8.GetByteCount($Ssid)
    if ($length -lt 1 -or $length -gt 32 -or $Ssid -match '[\x00-\x1f\x7f]') {
        throw 'SSID must contain 1-32 UTF-8 bytes and no control characters.'
    }
    if ($Password -notmatch '^[\x20-\x7e]{8,63}$' -and $Password -notmatch '^[0-9a-fA-F]{64}$') {
        throw 'Use a WPA2 password of 8-63 ASCII characters (or a 64-digit hexadecimal PSK).'
    }
}

function Get-WpaPsk([string]$Ssid, [string]$Password) {
    if ($Password -match '^[0-9a-fA-F]{64}$') { return $Password.ToLowerInvariant() }
    $derive = New-Object Security.Cryptography.Rfc2898DeriveBytes(
        $Password, ([Text.Encoding]::UTF8.GetBytes($Ssid)), 4096)
    try { ([BitConverter]::ToString($derive.GetBytes(32))).Replace('-', '').ToLowerInvariant() }
    finally { $derive.Dispose() }
}

if (-not $BootPath) {
    $candidates = @(Get-PSDrive -PSProvider FileSystem | Where-Object {
        (Test-Path -LiteralPath (Join-Path $_.Root 'user-data') -PathType Leaf) -and
        (Test-Path -LiteralPath (Join-Path $_.Root 'network-config') -PathType Leaf)
    })
    if ($candidates.Count -eq 1) {
        $BootPath = $candidates[0].Root
        Write-Host "Found Ubuntu boot partition: $BootPath"
    } else {
        $BootPath = Read-Host 'New Ubuntu SD boot partition path (for example F:\)'
    }
}
$root = (Resolve-Path -LiteralPath $BootPath).Path
if ($root.TrimEnd('\') -eq $env:SystemDrive.TrimEnd('\')) {
    throw 'The Windows system drive cannot be used.'
}
foreach ($marker in @('user-data', 'network-config')) {
    if (-not (Test-Path -LiteralPath (Join-Path $root $marker) -PathType Leaf)) {
        throw "Missing $marker. Choose the freshly flashed Ubuntu CIDATA partition, before its first boot."
    }
}

$phoneSsid = Read-Host 'Phone hotspot name (SSID)'
$phonePassword = Read-SecretText 'Phone hotspot password'
$labSsid = Read-Host 'Lab Wi-Fi SSID'
if (-not $labSsid) { throw 'Enter the exact lab Wi-Fi SSID.' }
$labPassword = Read-SecretText 'Lab Wi-Fi password'
Check-Wifi $phoneSsid $phonePassword
Check-Wifi $labSsid $labPassword
if ($phoneSsid -eq $labSsid) { throw 'Phone and lab SSIDs must be different.' }

$settings = @{
    version = 1
    phone = @{ ssid = $phoneSsid; password = $phonePassword }
    lab = @{ ssid = $labSsid; password = $labPassword }
} | ConvertTo-Json -Depth 4 -Compress
$encodedSettings = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($settings))
$phonePsk = Get-WpaPsk $phoneSsid $phonePassword

# First boot uses only the phone hotspot. After setup.py, both networks
# are managed by NetworkManager with a hotspot preference timer.
$network = @"
network:
  version: 2
  renderer: networkd
  wifis:
    wlan0:
      dhcp4: true
      optional: true
      access-points:
        $(Quote-Yaml $phoneSsid):
          password: $(Quote-Yaml $phonePsk)
"@

$userData = @"
#cloud-config
hostname: radxa-u22
manage_etc_hosts: true
users:
  - default
  - name: ubuntu
    groups: [adm, sudo, dialout, video, plugdev]
chpasswd:
  expire: true
  users:
    - name: ubuntu
      password: ubuntu
      type: text
ssh_pwauth: true
write_files:
  - path: /etc/radxa/wifi-profiles.json
    owner: root:root
    permissions: '0600'
    encoding: b64
    content: $encodedSettings
runcmd:
  - [systemctl, enable, --now, ssh]
"@

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
$encoding = New-Object Text.UTF8Encoding($false)
foreach ($item in @(@('network-config', $network), @('user-data', $userData))) {
    $path = Join-Path $root $item[0]
    Copy-Item -LiteralPath $path -Destination "$path.before-radxa-$stamp" -ErrorAction Stop
    [IO.File]::WriteAllText($path, (($item[1] -replace "`r`n", "`n") + "`n"), $encoding)
}
$phonePassword = $null
$phonePsk = $null
$labPassword = $null
$settings = $null
$encodedSettings = $null
$network = $null
$userData = $null
Write-Host 'DONE. Safely eject the SD card. Keep the phone hotspot ON for first boot.'
Write-Host 'First login: ubuntu / ubuntu; change this password when prompted.'
Write-Host 'The SD boot files contain Wi-Fi credentials. Do not share these files or their screenshots.'
