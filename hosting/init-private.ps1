$ErrorActionPreference = 'Stop'

$config = Join-Path $PSScriptRoot '.env.private'
if (Test-Path -LiteralPath $config) {
    throw "Private hosting is already configured at $config."
}
$caddy = Join-Path $PSScriptRoot '..\.tools\caddy\caddy.exe'
if (-not (Test-Path -LiteralPath $caddy -PathType Leaf)) {
    throw 'Place the official Caddy Windows executable at .tools\caddy\caddy.exe first.'
}
Write-Host 'Type a strong, unique Cliptric password twice when Caddy prompts.'
$output = & $caddy hash-password --algorithm bcrypt
if ($LASTEXITCODE -ne 0) {
    throw 'Caddy could not hash the password.'
}
$hash = [regex]::Match(($output -join "`n"), '\$2[aby]\$[0-9]{2}\$[./A-Za-z0-9]{53}')
if (-not $hash.Success) {
    throw 'Caddy did not return a bcrypt password hash.'
}

$bytes = New-Object byte[] 32
$rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
try {
    $rng.GetBytes($bytes)
} finally {
    $rng.Dispose()
}
$token = [BitConverter]::ToString($bytes).Replace('-', '').ToLowerInvariant()
$content = @(
    'CLIPPING_PUBLIC_ORIGIN=https://cliptric.com'
    ('CLIPPING_PASSWORD_HASH=' + $hash.Value)
    ('CLIPPING_GATEWAY_TOKEN=' + $token)
) -join "`n"
$file = [System.IO.File]::Open($config, [System.IO.FileMode]::CreateNew)
try {
    $data = [System.Text.Encoding]::UTF8.GetBytes($content + "`n")
    $file.Write($data, 0, $data.Length)
} finally {
    $file.Dispose()
}
Write-Host 'Private hosting configured. Browser username: cliptric'
