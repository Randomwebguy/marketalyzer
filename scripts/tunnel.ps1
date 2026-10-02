# Start the marketalyzer web interface and publish it through a Cloudflare quick
# tunnel (https://<random>.trycloudflare.com) on Windows. Needs cloudflared:
#   winget install --id Cloudflare.cloudflared
# Run from the project folder with the virtual environment active:
#   .venv\Scripts\Activate.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\tunnel.ps1          # real data
#   powershell -ExecutionPolicy Bypass -File scripts\tunnel.ps1 --demo   # demo mode
# Extra arguments go to marketalyzer-web.
param(
    [int]$Port = 8000,
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$WebArgs
)

if (-not (Get-Command cloudflared -ErrorAction SilentlyContinue)) {
    Write-Host "cloudflared bulunamadı. Kurulum: winget install --id Cloudflare.cloudflared"
    exit 1
}
if (-not (Get-Command marketalyzer-web -ErrorAction SilentlyContinue)) {
    Write-Host "marketalyzer-web bulunamadı. Önce sanal ortamı etkinleştirin: .venv\Scripts\Activate.ps1"
    exit 1
}
if (-not $env:MARKETALYZER_TOKEN) {
    $bytes = New-Object byte[] 16
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $env:MARKETALYZER_TOKEN = [Convert]::ToBase64String($bytes).TrimEnd("=").Replace("+", "-").Replace("/", "_")
}

$arguments = @("--port", "$Port")
if ($WebArgs) { $arguments += $WebArgs }
$web = Start-Process -FilePath "marketalyzer-web" -ArgumentList $arguments -PassThru -NoNewWindow

try {
    # cloudflared logs to stderr; print every line and the address once it appears.
    & cloudflared tunnel --no-autoupdate --url "http://127.0.0.1:$Port" 2>&1 | ForEach-Object {
        $line = "$_"
        Write-Host $line
        if ($line -match "(https://[a-z0-9-]+\.trycloudflare\.com)") {
            Write-Host ""
            Write-Host ">>> Arayüz adresi: $($Matches[1])/?token=$($env:MARKETALYZER_TOKEN)"
            Write-Host ">>> Bu adresi bilen herkes arayüze erişebilir; paylaşmayın."
            Write-Host ""
        }
    }
}
finally {
    if ($web -and -not $web.HasExited) { Stop-Process -Id $web.Id -Force }
}
