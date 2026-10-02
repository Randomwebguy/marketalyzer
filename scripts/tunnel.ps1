# Start the marketalyzer web interface and publish it through Cloudflare (Windows).
#
# Fixed address (needs a free Cloudflare account and a domain on Cloudflare):
#   cloudflared tunnel login        # once: pick the domain in the browser
#   powershell -ExecutionPolicy Bypass -File scripts\tunnel.ps1 -Hostname bist.alanadiniz.com
# The first run creates the tunnel and its DNS record and remembers the hostname,
# so later runs need no arguments:
#   powershell -ExecutionPolicy Bypass -File scripts\tunnel.ps1
# Tunnel made in the Cloudflare dashboard instead: set $env:CLOUDFLARE_TUNNEL_TOKEN
# (and give -Hostname to have the address printed).
# Without a domain, -Quick gives a new random https://*.trycloudflare.com address
# on every run. -Forget drops the remembered hostname.
#
# The access token is kept between runs; "marketalyzer-web --new-token" replaces it.
# Run from the project folder with the virtual environment active
# (.venv\Scripts\Activate.ps1). Other arguments go to marketalyzer-web, e.g. --demo.
# Saved as UTF-8 with a BOM: Windows PowerShell 5.1 reads BOM-less files as ANSI
# and garbles the Turkish messages.
param(
    [int]$Port = 8000,
    [string]$Hostname = "",
    [string]$TunnelName = "marketalyzer",
    [switch]$Quick,
    [switch]$Forget,
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$WebArgs
)

foreach ($tool in "cloudflared", "marketalyzer-web") {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        if ($tool -eq "cloudflared") {
            Write-Host "cloudflared bulunamadı. Kurulum: winget install --id Cloudflare.cloudflared"
        } else {
            Write-Host "marketalyzer-web bulunamadı. Önce sanal ortamı etkinleştirin: .venv\Scripts\Activate.ps1"
        }
        exit 1
    }
}

$dataDir = if ($env:MARKETALYZER_HOME) { $env:MARKETALYZER_HOME } else { Join-Path $HOME ".local\share\marketalyzer" }
New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
$savedFile = Join-Path $dataDir "tunnel.json"
if ($Forget -and (Test-Path $savedFile)) {
    Remove-Item $savedFile
    Write-Host "Kayıtlı tünel adresi silindi."
}
$saved = $null
if (Test-Path $savedFile) { $saved = Get-Content $savedFile -Raw | ConvertFrom-Json }
if (-not $Hostname -and -not $Quick -and $saved) {
    $Hostname = $saved.hostname
    if (-not $PSBoundParameters.ContainsKey("TunnelName") -and $saved.tunnel) { $TunnelName = $saved.tunnel }
}

if (-not $env:MARKETALYZER_TOKEN) {
    $env:MARKETALYZER_TOKEN = ((& marketalyzer-web --print-token) | Select-Object -Last 1).Trim()
}
$token = $env:MARKETALYZER_TOKEN

if ($Quick) { $mode = "quick" }
elseif ($env:CLOUDFLARE_TUNNEL_TOKEN) { $mode = "token" }
elseif ($Hostname) { $mode = "named" }
else { $mode = "quick" }

$cloudflaredArgs = @()
if ($mode -eq "named") {
    $cfDir = Join-Path $HOME ".cloudflared"
    if (-not (Test-Path (Join-Path $cfDir "cert.pem"))) {
        Write-Host "Sabit adres için önce bir kez Cloudflare'e giriş yapın:"
        Write-Host "  cloudflared tunnel login"
        Write-Host "Açılan sayfada $Hostname alan adını seçin, sonra bu scripti tekrar çalıştırın."
        exit 1
    }
    $tunnel = (& cloudflared tunnel list --name $TunnelName --output json | Out-String | ConvertFrom-Json) | Select-Object -First 1
    if (-not $tunnel) {
        Write-Host "'$TunnelName' tüneli oluşturuluyor…"
        & cloudflared tunnel create $TunnelName
        if ($LASTEXITCODE -ne 0) { Write-Host "Tünel oluşturulamadı."; exit 1 }
        $tunnel = (& cloudflared tunnel list --name $TunnelName --output json | Out-String | ConvertFrom-Json) | Select-Object -First 1
    }
    $credentials = Join-Path $cfDir "$($tunnel.id).json"
    if (-not (Test-Path $credentials)) {
        Write-Host "Tünelin kimlik dosyası bu bilgisayarda yok: $credentials"
        Write-Host "Tünel başka bir bilgisayarda oluşturulmuş olabilir. Farklı bir ad deneyin: -TunnelName marketalyzer-pc"
        exit 1
    }
    if (-not $saved -or $saved.hostname -ne $Hostname -or $saved.tunnel -ne $TunnelName) {
        Write-Host "DNS kaydı ekleniyor: $Hostname -> $TunnelName"
        & cloudflared tunnel route dns $TunnelName $Hostname
        if ($LASTEXITCODE -ne 0) {
            Write-Host "Uyarı: DNS kaydı eklenemedi. Kayıt zaten başka bir hedefi gösteriyorsa Cloudflare panelinden silin."
        }
        @{ hostname = $Hostname; tunnel = $TunnelName } | ConvertTo-Json | Set-Content -Path $savedFile -Encoding UTF8
    }
    $configFile = Join-Path $dataDir "cloudflared-$TunnelName.yml"
    $yaml = @"
tunnel: $($tunnel.id)
credentials-file: '$credentials'
ingress:
  - hostname: $Hostname
    service: http://127.0.0.1:$Port
  - service: http_status:404

"@
    # UTF-8 without a BOM, so a user folder with Turkish letters stays intact.
    [System.IO.File]::WriteAllText($configFile, $yaml, (New-Object System.Text.UTF8Encoding $false))
    $cloudflaredArgs = @("tunnel", "--no-autoupdate", "--config", $configFile, "run", $TunnelName)
} elseif ($mode -eq "token") {
    $cloudflaredArgs = @("tunnel", "--no-autoupdate", "run", "--token", $env:CLOUDFLARE_TUNNEL_TOKEN)
} else {
    $cloudflaredArgs = @("tunnel", "--no-autoupdate", "--url", "http://127.0.0.1:$Port")
}

$arguments = @("--port", "$Port")
if ($WebArgs) { $arguments += $WebArgs }
$web = Start-Process -FilePath "marketalyzer-web" -ArgumentList $arguments -PassThru -NoNewWindow

function Show-Address([string]$base) {
    Write-Host ""
    Write-Host ">>> Arayüz adresi: $base/?token=$token"
    Write-Host ">>> Bu adresi bilen herkes arayüze erişebilir; paylaşmayın."
    Write-Host ""
}

$script:shown = $false
try {
    # cloudflared logs to stderr; print every line and the address once it is known.
    & cloudflared @cloudflaredArgs 2>&1 | ForEach-Object {
        $line = "$_"
        Write-Host $line
        if ($script:shown) { return }
        if ($mode -eq "quick" -and $line -match "(https://[a-z0-9-]+\.trycloudflare\.com)") {
            Show-Address $Matches[1]
            Write-Host ">>> Bu adres her başlatmada değişir. Sabit adres için: scripts\tunnel.ps1 -Hostname bist.alanadiniz.com"
            Write-Host ""
            $script:shown = $true
        } elseif ($mode -ne "quick" -and $line -match "Registered tunnel connection") {
            if ($Hostname) { Show-Address "https://$Hostname" }
            else { Write-Host ">>> Tünel bağlandı. Adres: panelde tanımladığınız alan adı + /?token=$token" }
            $script:shown = $true
        }
    }
}
finally {
    if ($web -and -not $web.HasExited) { Stop-Process -Id $web.Id -Force }
}
