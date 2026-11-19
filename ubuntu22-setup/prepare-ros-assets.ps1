# Run on the Windows laptop from any directory with access to the official sources.
$ErrorActionPreference = 'Stop'
$rosdepDir = Join-Path $PSScriptRoot 'rosdep-data'
New-Item -ItemType Directory -Force -Path $rosdepDir | Out-Null
$rosdistro = 'https://raw.githubusercontent.com/ros/rosdistro/master'
$inputs = [ordered]@{
    'base.yaml'                = "$rosdistro/rosdep/base.yaml"
    'python.yaml'              = "$rosdistro/rosdep/python.yaml"
    'ruby.yaml'                = "$rosdistro/rosdep/ruby.yaml"
    'osx-homebrew.yaml'        = "$rosdistro/rosdep/osx-homebrew.yaml"
    'humble-distribution.yaml' = "$rosdistro/humble/distribution.yaml"
    'humble-cache.yaml.gz'     = 'https://repo.ros2.org/rosdistro_cache/humble-cache.yaml.gz'
}
function Save-UpstreamFile([string]$Url, [string]$Path, [string]$Sha256 = '') {
    $temp = "$Path.download"
    & curl.exe -fLsS --connect-timeout 15 --max-time 120 --retry 2 -o $temp $Url
    if ($LASTEXITCODE -ne 0) { throw "Download failed: $Url" }
    $item = Get-Item -LiteralPath $temp
    if ($item.Length -eq 0) { throw "Empty download: $Url" }
    $hash = (Get-FileHash -LiteralPath $temp -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($Sha256 -and $hash -ne $Sha256) { throw "Unexpected SHA-256: $Url" }
    Move-Item -LiteralPath $temp -Destination $Path -Force
    return @{ url = $Url; size = $item.Length; sha256 = $hash }
}
$deb = 'ros2-apt-source_1.3.0.jammy_all.deb'
$null = Save-UpstreamFile "https://github.com/ros-infrastructure/ros-apt-source/releases/download/1.3.0/$deb" `
    (Join-Path $PSScriptRoot $deb) '110b9a462d55252decb8b7c816f61c2ba0d9890ce5fb93ac504e97cae5860d76'
$manifest = @{ retrieved_utc = [DateTime]::UtcNow.ToString('o'); files = [ordered]@{} }
foreach ($name in $inputs.Keys) {
    Write-Host "Fetching $name"
    $manifest.files[$name] = Save-UpstreamFile $inputs[$name] (Join-Path $rosdepDir $name)
}
$json = $manifest | ConvertTo-Json -Depth 5
[IO.File]::WriteAllText((Join-Path $rosdepDir 'manifest.json'), $json + "`n", [Text.UTF8Encoding]::new($false))
Write-Host 'ROS ASSETS READY. Copy ubuntu22-setup to Radxa over SCP.'
