# marketalyzer

Borsa İstanbul (BIST) üzerinde yapay zeka destekli strateji denemeleri için bir çalışma alanı. Stratejiler önce geçmiş veriyle **backtest** edilir, sonra **gerçek para kullanılmadan** sanal bir portföyle canlı piyasada (paper trading) izlenir.

> Bu proje deneme ve öğrenme amaçlıdır. Gerçek emir göndermez ve yatırım tavsiyesi değildir.

## Yapı

```
providers/bist/          openbb-bist: OpenBB ODP V5 için BIST veri sağlayıcısı
marketalyzer/backtest/   backtesting.py üzerine kurulu BIST backtest motoru
marketalyzer/paper/      sanal hesapla paper trading simülatörü
marketalyzer/scripting/  TradingView Pine Script benzeri gösterge ve strateji dili
marketalyzer/ai/         OpenRouter üzerinden çalışan, araç kullanan yapay zeka asistanı
marketalyzer/services.py web API'si ve asistan araçlarının ortak işlemleri
marketalyzer/web/        web uygulaması (FastAPI + kurulabilir PWA)
scripts/tunnel.sh        arayüzü Cloudflare tüneliyle yayınlama
tests/                   testler (ağa çıkmaz, sentetik veri kullanır)
```

## Kurulum

```sh
uv sync                                  # uv ile (önerilen)
pip install -e providers/bist -e .       # veya pip ile
```

## Veri katmanı

Veri katmanı olarak [OpenBB Open Data Platform](https://github.com/openbq-org/openbb) kullanılır. `openbb-bist` sağlayıcısı BIST hisselerini, endekslerini ve TRY kurlarını OpenBB'nin standart komutlarına bağlar:

```python
from openbb import obb

prices = obb.equity.price.historical("THYAO", start_date="2024-01-01", provider="bist")
xu100 = obb.index.price.historical("XU100", provider="bist")
quote = obb.equity.price.quote("THYAO,GARAN", provider="bist")
```

Semboller, periyotlar ve veri kısıtları için [providers/bist/README.md](providers/bist/README.md) dosyasına bakın.

Türkiye makro verisi OpenBB'nin kendi sağlayıcılarından alınabilir: IMF (`country="TUR"`), FRED (`country="turkey"`, API anahtarı gerekir) ve JODI (`country="turkiye"`). OpenBB V5'in OECD komutları Türkiye için şu an hata veriyor.

## Backtest motoru

[backtesting.py](https://kernc.github.io/backtesting.py/) üzerine kuruludur ve BIST'e özgü ayarlarla gelir.

### Komut satırı

```sh
# Varsayılan: son 1 yıl, günlük barlar, SMA kesişimi (10/50)
marketalyzer-backtest THYAO

# Tarih aralığı, strateji parametreleri ve etkileşimli grafik
marketalyzer-backtest GARAN --start 2022-01-01 -s rsi_reversion -p lower=25 --plot garan.html

# Parametre optimizasyonu: son %30'luk dönem teste ayrılır
marketalyzer-backtest ASELS --start 2021-01-01 --optimize --grid fast=5:30:5 --grid slow=20:120:20

# Yapay zeka ajanları ve betikler için JSON çıktı
marketalyzer-backtest THYAO --json
```

`marketalyzer-backtest --help` tüm seçenekleri listeler.

### Python

```python
from marketalyzer.backtest import BistCosts, optimize_backtest, run_backtest

report = run_backtest(
    "THYAO",
    "sma_cross",
    start="2022-01-01",
    params={"fast": 20, "slow": 100},
    costs=BistCosts(commission_rate=0.001, min_commission=5),
)
report.summary()  # JSON'a çevrilebilir özet
report.trades  # işlem listesi (DataFrame)
report.plot("thyao.html")  # etkileşimli grafik

result = optimize_backtest("THYAO", "sma_cross", start="2021-01-01", holdout=0.3)
result.best_params, result.out_of_sample.summary()
```

Kendi stratejin bir `backtesting.Strategy` alt sınıfı olabilir: `run_backtest("THYAO", MyStrategy)`. Stratejiyi isimle çağırmak için `marketalyzer/backtest/strategies.py` içindeki `STRATEGIES` sözlüğüne ekle.

### BIST'e özgü varsayımlar

| Konu | Varsayılan | Not |
|---|---|---|
| Komisyon | binde 2 (`--commission 0.002`) | Aracı kurumuna göre değiştir. Alışta ve satışta ayrı alınır. |
| BSMV | komisyonun %5'i | |
| Asgari komisyon | 0 TL (`--min-commission`) | |
| Makas ve kayma | %0,1 (`--slippage 0.001`) | İşlem başına bir kez, girişte uygulanır. |
| Emir büyüklüğü | tam hisse (1 lot = 1 pay) | |
| Emir zamanı | sinyalden sonraki barın açılışı | `--trade-on-close` ile sinyal barının kapanışı (kapanış seansı). |
| Fiyatlar | bölünme, bedelsiz ve temettü düzeltmeli | Temettüler fiyata yansıdığı için getiri toplam getiridir. |
| Açığa satış | uyarı verilir | BIST'te açığa satış kısıtlı olduğu için örnek stratejiler sadece alım yapar. |

Bunların yanında motor şu kontrolleri yapar:
- **Şüpheli sıçramalar:** Günlük ve gün içi verilerde %20'yi aşan bir kapanıştan kapanışa hareket, BIST'in ±%10 fiyat limitini aştığı için uyarı üretir. Bu çoğunlukla düzeltilmemiş bir bölünme veya bedelsiz sermaye artırımıdır.
- **Enflasyon etkisi:** TL getirisi tek başına yanıltıcıdır. Rapor aynı dönemde XU100 getirisini, USD/TRY değişimini ve stratejinin **USD bazında getirisini** de gösterir.
- **Risksiz faiz:** Sharpe ve Sortino varsayılan olarak %0 faizle hesaplanır. TL'deki yüksek faiz ortamında `--risk-free 0.40` gibi bir oranla mevduat getirisine karşı ölçmek daha anlamlıdır.
- **Fiyat adımları:** Limit emirli stratejiler için `round_to_tick(fiyat, "down")` fiyatı BIST fiyat adımına yuvarlar.

### Dikkat edilmesi gerekenler

- **Aşırı uyum (overfitting):** Optimizasyon, parametreleri verinin ilk bölümünde seçer ve son `--holdout` oranlık dönemde test eder. Eğitim dönemi parlak, test dönemi zayıfsa strateji büyük ihtimalle geçmişe uydurulmuştur.
- **Taban ve tavan:** Fiyat tavana veya tabana kilitlendiğinde gerçekte emir gerçekleşmeyebilir. Bar verisi bunu göstermez.
- **Önbellek:** Bitiş tarihi geçmişte olan veriler `~/.cache/marketalyzer` altında saklanır (`MARKETALYZER_CACHE_DIR` ile değiştirilebilir). Yahoo düzeltmeleri sonradan değişirse bu klasörü sil.
- **Lisans:** backtesting.py AGPL-3.0 lisanslıdır. Kişisel kullanımda sorun yoktur. Proje bir ağ servisi olarak başkalarına sunulursa AGPL'nin kaynak kodu paylaşma şartı devreye girer.

## Paper trading (sanal hesap)

Gerçek para ve gerçek emir kullanılmaz. Sanal hesabın nakdi, pozisyonları, emirleri ve işlemleri tek bir SQLite dosyasında tutulur (`~/.local/share/marketalyzer/paper/<hesap>.sqlite`, `MARKETALYZER_HOME` ile değiştirilebilir).

```sh
marketalyzer-paper init --cash 100000                 # sanal hesap aç
marketalyzer-paper buy THYAO 100                      # piyasa emri
marketalyzer-paper buy GARAN 50 --limit 129.5         # limit emir
marketalyzer-paper sell THYAO 100 --stop 280 --gtc    # iptal edilene kadar geçerli stop emri
marketalyzer-paper status                             # nakit, pozisyonlar, açık emirler
marketalyzer-paper orders --all                       # emir geçmişi
marketalyzer-paper fills                              # gerçekleşen işlemler
marketalyzer-paper cancel 3                           # emir iptali

# Stratejiyi gecikmeli canlı veriyle çalıştır: her dakika yeni barları işler
marketalyzer-paper run THYAO GARAN -s sma_cross -p fast=20 -p slow=100

# Tek adım çalıştırıp çık (zamanlanmış görev veya yapay zeka ajanı için)
marketalyzer-paper run THYAO -s sma_cross --once

# Geçmiş günleri canlıymış gibi oynat (5 dakikalık barlarda son 60 gün)
marketalyzer-paper replay THYAO -s sma_cross --start 2026-09-01

# Günlük barlarla yıllarca geriye oynat; temettüler stopaj düşülerek nakit ödenir
marketalyzer-paper replay THYAO GARAN -s sma_cross --interval 1d --start 2020-01-01

# Günlük barlarla gün sonu çalıştırma: her akşam kapanıştan sonra bir kez
marketalyzer-paper run THYAO -s sma_cross --interval 1d --once
```

Her komut `--json` ile makinece okunabilir çıktı verir. Birden fazla hesap için `--account AD` kullanılır.

### Nasıl çalışır

- **Emir değerlendirme:** emirler, verildikleri andan sonra başlayan ilk bardan itibaren değerlendirilir.
  - Piyasa emri barın açılışından gerçekleşir.
  - Limit emir, bar limit fiyata değdiğinde gerçekleşir. Bar limitin ötesinde açılırsa daha iyi olan açılış fiyatı kullanılır.
  - Stop emri, bar stop fiyatına değdiğinde piyasa emrine dönüşür.
- **Gecikme:** Yahoo verisi yaklaşık 15 dakika gecikmelidir. Bu dolum fiyatını değil, yalnızca onayın zamanını etkiler. Saat 14:00'te verilen piyasa emri 14:00 barının açılışından gerçekleşir, ama bu bar tamamlanıp veri gecikmesi geçtikten sonra, yaklaşık 14:20'de görünür.
- **Maliyetler:** komisyon, BSMV ve asgari komisyon backtest'teki gibi hesaplanır. Gidiş-dönüş kayma oranının yarısı her piyasa ve stop işleminde uygulanır, fiyat da BIST fiyat adımına aleyhe yuvarlanır.
- **Emir kuralları:**
  - Limit ve stop fiyatları fiyat adımına uymalıdır.
  - Elde olmayan hisse satılamaz; yalnızca uzun pozisyon açılabilir.
  - Alımlarda satın alma gücü, açık emirler düşülerek kontrol edilir.
- **Geçerlilik:** günlük (`day`) emirler seans bitince, `--gtc` emirler iptal edilene kadar geçerlidir. 18:10'dan sonra verilen günlük emir bir sonraki seansa kalır.
- **Strateji sinyalleri:** backtest'teki strateji sınıflarıyla, tamamlanmış günlük barlardan hesaplanır. Sinyal kapanışta oluşur, emir bir sonraki seansın ilk barında gerçekleşir; zamanlama backtest ile aynıdır.
  - Strateji pozisyona girmek istediğinde, sembolün özsermayedeki payının %98'iyle alır. %2'lik nakit tamponu, fiyat emir dolmadan önce yükselirse emrin reddedilmemesi içindir.
  - Nakde dönmek istediğinde tüm pozisyonu satar. `run`, verilen sembollerdeki pozisyonların tamamını yönetir; elle açılmış pozisyonlar da buna dahildir.
- **Eşzamanlı kullanım:** `run` çalışırken başka bir terminalden veya bir yapay zeka ajanından aynı hesaba güvenle emir verilebilir.

### Yürüyen pencere testi (walk-forward)

Geçmişten ileriye dönük en dürüst ölçüm. Her pencerede parametreler önceki `--train` işlem gününde backtest ile optimize edilir. Sonraki `--test` gün, bu parametrelerle aynı sanal hesapta günlük barlarla paper trade edilir. Ardından pencere `--test` gün kaydırılır. Sonuçta yalnızca bu ileri dönemler sayılır; her dönem, parametrelerin seçildiği sırada görülmemiş veridir.

```sh
marketalyzer-paper walkforward THYAO -s sma_cross --start 2018-01-01 --train 504 --test 126
```

Çıktıda her pencerenin seçilen parametreleri, ileri dönem getirisi ve aynı dönemdeki al ve tut getirisi yer alır. Sonunda toplam getiri, en büyük düşüş, işlem sayısı, komisyon ve net temettü raporlanır. Parametreler eğitimde iyi görünüp ileri dönemlerde sürekli al ve tutun gerisinde kalıyorsa, strateji geçmişe uydurulmuştur.

### Kısıtlar

- Kısmi gerçekleşme yoktur. Emir, hacimden bağımsız olarak tamamen dolar; az işlem gören hisselerde sonuç iyimser olur.
- Taban ve tavan fiyat limitleri, açılış ve kapanış seansı eşleşmeleri ve emir defteri derinliği modellenmez.
- Temettüler yalnızca günlük barlarla (`--interval 1d`) nakit olarak ödenir; varsayılan stopaj oranı %15'tir (`--dividend-tax`, güncel oranı kontrol edin). Dakikalık barlarda temettü yansımaz.
- Bölünme ve bedelsiz sermaye artırımlarında replay, bölünmeye göre düzeltilmiş fiyatlarla çalıştığı için sonuç tutarlıdır. Canlı çalışan bir hesapta ise bölünme günü pozisyon adedi değişmez; bunu elle düzeltmek gerekir.

## Script dili (Pine Script benzeri)

Göstergeler ve stratejiler TradingView Pine Script v5'e çok yakın bir dille yazılır; yaygın scriptler küçük değişikliklerle ya da hiç değiştirilmeden çalışır. Scriptler `marketalyzer/scripting/library/` altındaki hazır kütüphaneyle birlikte gelir (SMA kesişimi, RSI dönüşü, MACD + trend filtresi, Bollinger dönüşü, Supertrend, Donchian kırılımı, RSI, MACD, Bollinger bantları). Kendi scriptleriniz `~/.local/share/marketalyzer/scripts/` altında saklanır.

```pine
//@version=5
strategy("RSI + trend", overlay=false)
length = input.int(14, "RSI", minval=7, maxval=21, step=7)   // minval/maxval/step = optimizasyon ızgarası
r = ta.rsi(close, length)
plot(r, "RSI", color=color.purple)
hline(30, "Aşırı satım")
if ta.crossover(r, 30) and close > ta.sma(close, 200)
    strategy.entry("Al", strategy.long)
    strategy.exit("Stop", "Al", stop=close * 0.93)
if r > 70
    strategy.close("Al")
```

- **Desteklenenler:** `indicator()`/`strategy()`, `input.*` (parametre adı, atandığı değişkenin adıdır), `=`/`:=`/`+=`, `if / else if / else`, `var`, `x[1]` geçmiş referansı, `?:`, `and/or/not`, `na`/`nz()`, `[a, b] = ...`, kullanıcı fonksiyonları (`f(x) => ...`), 50'yi aşkın `ta.*` fonksiyonu (sma, ema, rma, wma, hma, rsi, macd, bb, stoch, atr, dmi, supertrend, sar, kc, vwap, pivothigh, crossover, barssince, valuewhen …), `math.*`, `plot`, `plotshape`, `hline`, `alertcondition`, `strategy.entry/close/exit` ve `strategy.position_size`.
- **Desteklenmeyenler:** döngüler, diziler, `request.security`, çizim nesneleri (etiket, çizgi, kutu, tablo; yok sayılır) ve iz süren stop. Hatalar satır numarasıyla Türkçe raporlanır.
- **Nasıl çalışır:** İfadelerin çoğu numpy ile tüm barlar üzerinde bir kerede hesaplanır, bu yüzden optimizasyonlarda bile hızlıdır. Kendi geçmişine bağlı ifadeler (`var` değişkenleri, `x := nz(x[1]) + 1`, `strategy.position_size`) Pine'daki gibi bar bar yorumlanır. Her iki aşama da nedenseldir; script hiçbir barda gelecekteki veriyi göremez.
- **Strateji olarak:** `strategy()` ile bildirilen her script bir backtesting.py stratejisine dönüşür ve `script:<ad>` adıyla backtest, optimizasyon, walk-forward, tarama ve paper trading'de kullanılır. Emirler sinyal barından sonraki açılışta gerçekleşir; açığa satış yoktur (`strategy.short` girişi pozisyonu kapatır) ve komisyon her zaman BIST maliyetleridir. Hazır `sma_cross` scripti, yerleşik `SmaCross` stratejisiyle birebir aynı işlemleri üretir.

```sh
marketalyzer-backtest THYAO --strategy script:rsi_reversion --optimize
```

## Yapay zeka asistanı (OpenRouter)

Asistan, [OpenRouter](https://openrouter.ai) üzerinden seçtiğiniz modeli kullanır ve uygulamanın araçlarıyla çalışır: piyasa özeti, fiyatlar ve fiyat geçmişi, teknik analiz özeti, script yazma/doğrulama/kaydetme/çalıştırma, çok hisseli tarama, backtest, optimizasyon (holdout testiyle), walk-forward ve sanal hesap. Her sayıyı araçlardan alacak, aşırı uyum ve az işlem sayısı gibi riskleri belirtecek ve yatırım tavsiyesi vermeyecek şekilde yönlendirilir.

- **API anahtarı:** [openrouter.ai/keys](https://openrouter.ai/keys) adresinden alınan anahtar arayüzde **Ayarlar → Yapay zeka** bölümüne girilir ya da sunucuyu başlatırken `OPENROUTER_API_KEY` ortam değişkeniyle verilir. Anahtar sunucuda yalnızca sizin okuyabileceğiniz bir dosyada (`~/.local/share/marketalyzer/ai/settings.json`, izin 600) saklanır, tarayıcıya ve depoya hiçbir zaman gönderilmez; arayüzde yalnızca son 4 karakteri görünür.
- **Model:** Ayarlar'da OpenRouter'daki araç kullanabilen modeller fiyatlarıyla listelenir. Seçilmezse araç kullanabilen en yeni Claude Sonnet modeli otomatik seçilir. `MARKETALYZER_AI_MODEL` ile de sabitlenebilir.
- **Sanal emir izni:** Asistanın sanal hesapta emir verip iptal edebilmesi için Ayarlar'da ayrıca izin verilmelidir (varsayılan kapalı). Gerçek emir hiçbir durumda yoktur.
- **Maliyet:** Kullanım ücreti OpenRouter hesabınızdan düşer; her sohbetin token sayısı ve maliyeti arayüzde gösterilir.
- Sohbetler `~/.local/share/marketalyzer/ai/conversations/` altında saklanır. Yanıtlar sunucudan akış (SSE) olarak gelir; uzun backtestlerde tünelin bağlantıyı kesmemesi için düzenli canlılık sinyali gönderilir.

## Web uygulaması ve Cloudflare tüneli

Masaüstünde pencere çerçeveli, kenar çubuklu bir uygulama; telefonda alt sekme çubuğu ve alttan açılan sayfalarla yerel uygulama hissi veren bir arayüz. Kenar çubuğundaki **Piyasa / Lab** anahtarı iki çalışma alanı arasında geçiş yapar:

- **Panel:** dot-matrix tahmini bakiye (TL/USD, gizlenebilir), günlük K/Z, pozisyon kartları (al/sat/analiz), yapay zeka kartı, özsermaye ile BIST 100 karşılaştırması, seans saatli hızlı emir kartı, tutar → lot hesaplayıcı.
- **Piyasa:** izleme listesi, mum/çizgi grafik, fiyat grafiğine ya da ayrı panellere eklenebilen script göstergeleri, teknik özet (RSI, stokastik, ADX, Bollinger %B, 52 hafta aralığı, hacim, hareketli ortalamalar, pivot destek/direnç).
- **Emirler / İşlem:** açık ve geçmiş emirler, pozisyonlar, dağılım, gerçekleşen işlemler, emir fişi, strateji ile işleme ve hesap ayarları. Kenar çubuğunda açık emir kartı ve seans yayı.
- **Script editörü:** sözdizimi renklendirme, satır numaraları, otomatik tamamlama, anlık derleme ve hata satırı, parametre formu, konsol, fonksiyon başvurusu; grafikte çalıştırma, backtest, optimizasyon ve walk-forward.
- **Backtest, Walk-forward, Tarama:** hazır stratejiler ve script stratejileriyle.
- **Asistan:** sohbet geçmişi, araç çağrılarının canlı gösterimi, Markdown yanıtlar, scriptleri tek tıkla editörde açma, sembol ve editördeki scripti bağlam olarak ekleme.
- **Ayarlar:** OpenRouter anahtarı, model seçimi, emir izni, görünüm.

<kbd>⌘/Ctrl</kbd>+<kbd>K</kbd> komut paletini açar (sembol, sayfa, script ve eylem araması). <kbd>B</kbd> emir, <kbd>A</kbd> asistan, <kbd>H</kbd> bakiyeleri gizle; editörde <kbd>Ctrl</kbd>+<kbd>↵</kbd> çalıştırır, <kbd>Ctrl</kbd>+<kbd>S</kbd> kaydeder.

```sh
marketalyzer-web                 # http://127.0.0.1:8000/?token=... adresini yazdırır
marketalyzer-web --demo          # internetsiz deneme: sentetik fiyatlar, ayrı "demo" hesabı
OPENROUTER_API_KEY=sk-or-... scripts/tunnel.sh   # anahtarla başlatıp Cloudflare tüneliyle yayınlar
scripts/tunnel.sh --demo         # ek argümanlar marketalyzer-web'e geçer (burada demo modu)
```

Uygulama bir erişim anahtarıyla açılır. Adresteki `?token=...` bir kez kullanıldığında ya da giriş sayfasına anahtar yazıldığında tarayıcı onu 30 gün hatırlar; **Çıkış** bağlantısı çerezi siler. Sabit bir anahtar için `MARKETALYZER_TOKEN` ortam değişkeni kullanılabilir.

**Uygulama olarak kurma (PWA):** tünel adresi HTTPS olduğu için uygulama telefona ve masaüstüne kurulabilir.
- iPhone: Safari'de Paylaş → Ana Ekrana Ekle. Ana ekrandan ilk açılışta erişim anahtarını bir kez girin; ana ekran uygulamaları Safari'nin çerezlerini paylaşmaz.
- Android: Chrome menüsü → Uygulamayı yükle.
- Masaüstü (Chrome/Edge): adres çubuğundaki yükle simgesi.

**Windows:** [uv](https://docs.astral.sh/uv/) ve cloudflared'ı kurun (`winget install astral-sh.uv` ve `winget install --id Cloudflare.cloudflared`), sonra PowerShell'de:

```powershell
uv sync
.venv\Scripts\Activate.ps1
$env:OPENROUTER_API_KEY = "sk-or-..."      # isteğe bağlı; Ayarlar'dan da girilebilir
powershell -ExecutionPolicy Bypass -File scripts\tunnel.ps1          # ya da sonuna --demo
```

`scripts/tunnel.sh` için [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/) kurulu olmalıdır. Betik geçici bir `https://<rastgele>.trycloudflare.com` adresi açar ve anahtarla birlikte tam adresi yazdırır. Adresi bilen herkes uygulamaya erişebileceği için paylaşmayın. Tünel, betik çalıştığı sürece açık kalır.

Arayüzün yönettiği paper hesap `web` adını taşır (demo modunda `demo`). Komut satırından `marketalyzer-paper --account web status` ile de görülebilir. Uygulama ikonları `scripts/make_icons.py` ile yeniden üretilebilir. Yazı tipleri (Geist, Geist Mono, Doto) SIL Open Font License ile `web/static/fonts/` altında birlikte gelir, böylece uygulama çevrimdışı da aynı görünür.

Grafik renkleri, renk körlüğüne karşı doğrulanmış bir paletten seçildi. Yükseliş ve düşüş, renge ek olarak ▲/▼ işaretleri ve içi boş/dolu mumlarla da ayırt edilir. Her grafiğin bir tablo görünümü vardır. Saatler cihazın saat diliminden bağımsız olarak İstanbul saatiyle gösterilir.

## Geliştirme

```sh
pytest                                  # tüm testler (OpenRouter sahte akışla test edilir)
(cd providers/bist && pytest)           # sağlayıcı testleri
ruff check . && ruff format --check .
```

Testler ağa çıkmaz. Fiyatlar sentetik veridir.

## Yol haritası

1. **Veri katmanı:** `openbb-bist` sağlayıcısı. ✅
2. **Backtest motoru:** BIST maliyetleri, fiyat adımları, optimizasyon ve test dönemi ayrımı, USD bazında getiri. ✅
3. **Paper trading:** SQLite tabanlı sanal hesap, bar tabanlı emir eşleştirme, strateji döngüsü, günlük ve dakikalık replay, temettü ödemeleri ve walk-forward testi. ✅ Sonraki adımlar: kısmi gerçekleşme, taban/tavan limitleri ve çok sembollü walk-forward. Gerçek zamanlıya yakın test için lisanslı bir veri kaynağı gerekir.
4. **Script dili:** Pine Script benzeri gösterge ve stratejiler, editör, tarama. ✅
5. **Yapay zeka katmanı:** OpenRouter üzerinden, uygulamanın araçlarıyla stratejileri yalnızca backtest ve paper trading ortamında öneren, yazan, deneyen ve değerlendiren asistan. ✅ Sonraki adımlar: zamanlanmış görevler (her akşam tarama ve özet), script alarmlarının bildirim olarak gönderilmesi, çoklu zaman dilimi (`request.security`).
