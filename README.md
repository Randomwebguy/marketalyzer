# marketalyzer

Borsa İstanbul (BIST) üzerinde yapay zeka destekli strateji denemeleri için bir çalışma alanı. Stratejiler önce geçmiş veriyle **backtest** edilir, sonra **gerçek para kullanılmadan** sanal bir portföyle canlı piyasada (paper trading) izlenir.

> Bu proje deneme ve öğrenme amaçlıdır. Gerçek emir göndermez ve yatırım tavsiyesi değildir.

## Yapı

```
providers/bist/          openbb-bist: OpenBB ODP V5 için BIST veri sağlayıcısı
marketalyzer/backtest/   backtesting.py üzerine kurulu BIST backtest motoru
marketalyzer/paper/      sanal hesapla paper trading simülatörü
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

### Kısıtlar

- Kısmi gerçekleşme yoktur. Emir, hacimden bağımsız olarak tamamen dolar; az işlem gören hisselerde sonuç iyimser olur.
- Taban ve tavan fiyat limitleri, açılış ve kapanış seansı eşleşmeleri ve emir defteri derinliği modellenmez.
- Temettüler sanal hesaba nakit olarak yansımaz ve bölünmeler pozisyon adedini değiştirmez. Bu olayları kapsayan dönemlerde sonucu elle düzeltin.

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
3. **Paper trading:** SQLite tabanlı sanal hesap, bar tabanlı emir eşleştirme, strateji döngüsü ve replay. ✅ Sonraki adımlar: temettü ve bölünme olaylarının hesaba yansıması, kısmi gerçekleşme ve taban/tavan limitleri. Gerçek zamanlıya yakın test için lisanslı bir veri kaynağı gerekir.
4. **Yapay zeka katmanı:** `marketalyzer-backtest --json`, `marketalyzer-paper --json` ve OpenBB'nin MCP sunucusu (`openbb-mcp`) üzerinden çalışan, stratejileri yalnızca backtest ve paper trading ortamında öneren, deneyen ve değerlendiren bir ajan.
