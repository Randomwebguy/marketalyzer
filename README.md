# marketalyzer

Borsa İstanbul (BIST) üzerinde yapay zeka destekli strateji denemeleri için bir çalışma alanı. Stratejiler önce geçmiş veriyle **backtest** edilir, sonra **gerçek para kullanılmadan** sanal bir portföyle canlı piyasada (paper trading) izlenir.

> Bu proje deneme ve öğrenme amaçlıdır. Gerçek emir göndermez ve yatırım tavsiyesi değildir.

## Yapı

```
providers/bist/          openbb-bist: OpenBB ODP V5 için BIST veri sağlayıcısı
marketalyzer/backtest/   backtesting.py üzerine kurulu BIST backtest motoru
marketalyzer/paper/      sanal hesapla paper trading simülatörü
marketalyzer/web/        web uygulaması (FastAPI + kurulabilir PWA)
scripts/tunnel.sh        arayüzü Cloudflare tüneliyle yayınlama
tests/                   backtest ve paper trading testleri
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

## Web uygulaması ve Cloudflare tüneli

Karanlık temalı, uygulama hissi veren bir arayüz. Masaüstünde kenar çubuğu ve kart (widget) ızgarası, telefonda alt sekme çubuğu kullanılır. Dört bölümden oluşur:

- **Panel:** toplam varlık ve özsermaye grafiği, K/Z kutuları, portföy dağılımı, mini grafikli izleme listesi, mum grafiği (1 gün – 5 yıl), pozisyonlar ve son işlemler.
- **İşlem:** al/sat emri (piyasa, limit, stop; fiyat adımı kontrolü ve tahmini maliyetle), açık ve geçmiş emirler, "veriyi işle" ve hesap ayarları.
- **Backtest:** strateji seçimi ve parametreleri, optimizasyon, strateji ile al-tut karşılaştırma grafiği, XU100 ve USD bazında getiri, işlem listesi.
- **Walk-forward:** pencere pencere ileriye dönük test ve sonuçları.

```sh
marketalyzer-web                 # http://127.0.0.1:8000/?token=... adresini yazdırır
marketalyzer-web --demo          # internetsiz deneme: sentetik fiyatlar, ayrı "demo" hesabı
scripts/tunnel.sh                # uygulamayı başlatır ve Cloudflare tüneliyle yayınlar
```

Uygulama bir erişim anahtarıyla açılır. Adresteki `?token=...` bir kez kullanıldığında ya da giriş sayfasına anahtar yazıldığında tarayıcı onu 30 gün hatırlar. Sabit bir anahtar için `MARKETALYZER_TOKEN` ortam değişkeni kullanılabilir.

**Uygulama olarak kurma (PWA):** tünel adresi HTTPS olduğu için uygulama telefona ve masaüstüne kurulabilir.
- iPhone: Safari'de Paylaş → Ana Ekrana Ekle. Ana ekrandan ilk açılışta erişim anahtarını bir kez girin; ana ekran uygulamaları Safari'nin çerezlerini paylaşmaz.
- Android: Chrome menüsü → Uygulamayı yükle.
- Masaüstü (Chrome/Edge): adres çubuğundaki yükle simgesi.

`scripts/tunnel.sh` için [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/) kurulu olmalıdır. Betik geçici bir `https://<rastgele>.trycloudflare.com` adresi açar ve anahtarla birlikte tam adresi yazdırır. Adresi bilen herkes uygulamaya erişebileceği için paylaşmayın. Tünel, betik çalıştığı sürece açık kalır.

Arayüzün yönettiği paper hesap `web` adını taşır (demo modunda `demo`). Komut satırından `marketalyzer-paper --account web status` ile de görülebilir. Uygulama ikonları `scripts/make_icons.py` ile yeniden üretilebilir.

Grafik renkleri, renk körlüğüne karşı doğrulanmış bir paletten seçildi. Yükseliş ve düşüş, renge ek olarak ▲/▼ işaretleri ve içi boş/dolu mumlarla da ayırt edilir. Her grafiğin bir tablo görünümü vardır. Saatler cihazın saat diliminden bağımsız olarak İstanbul saatiyle gösterilir.

## Geliştirme

```sh
pytest                                  # backtest ve paper trading testleri
(cd providers/bist && pytest)           # sağlayıcı testleri
ruff check . && ruff format --check .
```

Testler ağa çıkmaz. Fiyatlar sentetik veridir.

## Yol haritası

1. **Veri katmanı:** `openbb-bist` sağlayıcısı. ✅
2. **Backtest motoru:** BIST maliyetleri, fiyat adımları, optimizasyon ve test dönemi ayrımı, USD bazında getiri. ✅
3. **Paper trading:** SQLite tabanlı sanal hesap, bar tabanlı emir eşleştirme, strateji döngüsü, günlük ve dakikalık replay, temettü ödemeleri ve walk-forward testi. ✅ Sonraki adımlar: kısmi gerçekleşme, taban/tavan limitleri ve çok sembollü walk-forward. Gerçek zamanlıya yakın test için lisanslı bir veri kaynağı gerekir.
4. **Yapay zeka katmanı:** `marketalyzer-backtest --json`, `marketalyzer-paper --json` ve OpenBB'nin MCP sunucusu (`openbb-mcp`) üzerinden çalışan, stratejileri yalnızca backtest ve paper trading ortamında öneren, deneyen ve değerlendiren bir ajan.
