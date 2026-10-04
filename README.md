# marketalyzer

Borsa İstanbul (BIST) üzerinde yapay zeka destekli strateji denemeleri için bir çalışma alanı. Stratejiler önce geçmiş veriyle **backtest** edilir, sonra **gerçek para kullanılmadan** sanal bir portföyle canlı piyasada (paper trading) izlenir.

> Bu proje deneme ve öğrenme amaçlıdır. Gerçek emir göndermez ve yatırım tavsiyesi değildir.

## Yapı

```
providers/bist/          openbb-bist: OpenBB ODP V5 için BIST veri sağlayıcısı
marketalyzer/backtest/   backtesting.py üzerine kurulu BIST backtest motoru
marketalyzer/paper/      sanal hesapla paper trading simülatörü
marketalyzer/scripting/  TradingView Pine Script benzeri gösterge ve strateji dili
marketalyzer/ai/         OpenRouter ya da fal.ai üzerinden çalışan, araç kullanan yapay zeka asistanı
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

## Yapay zeka asistanı (OpenRouter ya da fal.ai)

Asistan, [OpenRouter](https://openrouter.ai) ya da [fal.ai](https://fal.ai) üzerinden seçtiğiniz modeli kullanır ve uygulamanın araçlarıyla çalışır: piyasa özeti, fiyatlar ve fiyat geçmişi, teknik analiz özeti, script yazma/doğrulama/kaydetme/çalıştırma, çok hisseli tarama, backtest, optimizasyon (holdout testiyle), walk-forward ve sanal hesap. Her sayıyı araçlardan alacak, aşırı uyum ve az işlem sayısı gibi riskleri belirtecek ve yatırım tavsiyesi vermeyecek şekilde yönlendirilir.

- **Sağlayıcı:** **Ayarlar → Yapay zeka** bölümünde OpenRouter ya da fal.ai seçilir; asistan, script yazma, kör test ve canlı karar seçili sağlayıcıyla çalışır. fal.ai, OpenRouter'ın API'sini ve model kimliklerini (`google/gemini-…`, `anthropic/claude-…`) kendi adresinde (`https://fal.run/openrouter/router/openai/v1`) sunar ve ücreti fal.ai hesabınızdan alır. Seçim yapılmamışsa yalnızca fal.ai anahtarı varken fal.ai, aksi halde OpenRouter kullanılır.
- **API anahtarı:** Her sağlayıcının kendi anahtarı vardır: OpenRouter için [openrouter.ai/keys](https://openrouter.ai/keys), fal.ai için [fal.ai/dashboard/keys](https://fal.ai/dashboard/keys). Anahtar Ayarlar'a girilir ya da sunucuyu başlatırken `OPENROUTER_API_KEY` / `FAL_KEY` ortam değişkeniyle verilir. Anahtarlar sunucuda yalnızca sizin okuyabileceğiniz bir dosyada (`~/.local/share/marketalyzer/ai/settings.json`, izin 600) saklanır, tarayıcıya ve depoya hiçbir zaman gönderilmez; arayüzde yalnızca son 4 karakteri görünür. Yanlış alana yapıştırılan anahtar (ör. fal.ai alanına `sk-or-…`) kaydedilmez.
- **Model:** Ayarlar'da OpenRouter'daki araç kullanabilen modeller fiyatlarıyla listelenir; fal.ai'nin model listesi olmadığından fal.ai seçiliyken de bu liste kullanılır. Seçilmezse araç kullanabilen en yeni Claude Sonnet modeli otomatik seçilir. `MARKETALYZER_AI_MODEL` ile de sabitlenebilir.
- **Anahtar testi:** OpenRouter anahtarının kullanımı ve limiti gösterilir. fal.ai'de böyle bir uç nokta olmadığından en ucuz hızlı modele (Gemini Flash Lite) tek token'lık bir soru sorulur.
- **Sanal emir izni:** Asistanın sanal hesapta emir verip iptal edebilmesi için Ayarlar'da ayrıca izin verilmelidir (varsayılan kapalı). Gerçek emir hiçbir durumda yoktur.
- **Maliyet:** Kullanım ücreti seçili sağlayıcıdaki hesabınızdan düşer; her sohbetin token sayısı ve sağlayıcının bildirdiği maliyet arayüzde gösterilir.
- Sohbetler `~/.local/share/marketalyzer/ai/conversations/` altında saklanır. Yanıtlar sunucudan akış (SSE) olarak gelir; uzun backtestlerde tünelin bağlantıyı kesmemesi için düzenli canlılık sinyali gönderilir.

## AI Strateji: sinyal araştırması, yapay zekanın yazdığı script ve kör test

Web arayüzünde **Lab → AI Strateji** sayfası dört adımlı bir akış sunar. 1–8 hisse seçilir (varsayılan hedef sepet: ENKAI, TUPRS, THYAO, BIMAS, ASELS; "THY", "BIM", "ASELSAN", "TÜPRAŞ" gibi adlar koda çevrilir), bir test başlangıcı, bir bitiş ve bir eğitim süresi (1/2/3/5 yıl) belirlenir.

1. **Sinyal araştırması** (`marketalyzer/research.py`): eğitim dönemindeki getiri, oynaklık, maksimum düşüş, beta, momentum/ortalamaya dönüş eğilimi (getirilerin otokorelasyonu), boşluk ve bar aralığı, gün etkisi ve likidite. Yaklaşık 35 aday sinyalin (trend, kırılım, momentum, dönüş, hacim, fiyat hareketi) ardından 5/10/20 barlık getirisi, dönem ortalamasına göre fazla getirisi, isabeti ve kaba t değeri; hisseler arası korelasyon. Yahoo emir defteri sunmadığı için "derinlik" günlük TL hacmi, sıfır hacimli gün oranı ve Amihud likidite ölçüsüyle tahmin edilir. Yalnızca test başlangıcından önceki barlar okunur.
2. **Pine Script oluşturma** (`marketalyzer/ai/author.py`): asistan modeli (varsayılan en yeni Claude Sonnet) araştırmanın anonim özetinden (hisseler "Hisse A, B…", tarih yok) bir strateji scripti yazar. Script derlenir, örnek veride ve her hissenin eğitim barlarında denenir; derleme hatası, giriş üretmeme ya da aşırı sık giriş varsa hata modele geri verilir (en fazla 3 deneme). Sonuç `ai_<hisseler>_<tarih>` adıyla kaydedilir ve editörde açılabilir.
3. **Kör backtest** (`marketalyzer/blind.py`): test dönemi bar bar ilerler. Stratejinin bir giriş/çıkış sinyali verdiği (isteğe bağlı olarak her 5/10/20 barda bir yeniden sorulan) barda hızlı karar modeli, **yalnızca o bara kadar kesilmiş veriden** hazırlanan anonim bir görünümle AL/BEKLE ya da SAT/TUT der. Emir sonraki barın açılışında, BIST komisyonu, BSMV ve kaymayla gerçekleşir. Sermaye hisselere eşit bölünür, yalnızca uzun pozisyon açılır. Aynı sinyallerin yapay zekasız işlenmesi ("sadece sinyaller"), al-tut ve XU100 ile karşılaştırılır.
4. **Canlı karar:** seçili script ve modelle her hissenin son barı için karar alınır; sanal hesapta tutulan hisselere SAT/TUT, diğerlerine AL/BEKLE sorulur. Emir otomatik verilmez, emir fişi açılır.

**Kör test güvenceleri.** Model hiçbir kararda karar barından sonraki veriyi görmez: veri o bara kadar kesilerek verilir, strateji sinyali de her kararda kesilmiş veri üzerinde yeniden hesaplanır ve tam veriyle farklı çıkarsa karar verilmez. Modelin hafızasındaki piyasa bilgisini kullanmaması için hisse adı, tarih ve fiyat seviyesi gönderilmez (fiyatlar yüzde ve 100 tabanlı); gönderilen her görünüm hisse kodu ve tarih için ayrıca taranır. Sonuçta bir denetim kartı karar, yeniden hesaplama, uyuşmazlık ve sızıntı sayılarını gösterir. Testler, test bitişini ileri almanın önceki kararların hiçbirini değiştirmediğini doğrular.

**Strateji bağlamı ve veto.** Karar modeli her sinyali stratejinin kendi mantığıyla değerlendirir: test başında scriptin iki cümlelik özeti ve türü (dönüş, trend, kırılım) bir kez sorulup önbelleğe alınır, stratejinin test öncesindeki yapay zekasız sonucu (işlem sayısı, kazançlı oran, ortalama getiri) her görünüme eklenir. Talimat sinyali varsayılan olarak uygulatır; stratejinin aradığı koşullar (ör. dönüş stratejisinde düşüş ve aşırı satım) ret nedeni değildir, yalnızca belirgin ek risk reddedilir. Kârlı bir sinyali kaçırmak da hata sayılır.

**Trend takibi.** Script yazarının varsayılan hedefi (hedef boş bırakılırsa) trend takibidir: yükselen trendde pozisyonda kalmak, trend bozulunca çıkmak ve her hissede çeyrekte en az 2 işlem. Kör test sayfasının varsayılan scripti `supertrend_sik`tir (Supertrend, çarpan 2). ENKAI, TUPRS, THYAO, BIMAS, ASELS üzerinde 1 yıl eğitimle, yapay zekasız (sadece sinyaller) ölçümler:

| Script | 2024-10 → 2025-10 | 2025-10 → 2026-10 |
|---|---|---|
| Dönüş (düşüşte alım, yapay zekanın ilk yazdığı) | %+15,8 · 27 işlem | %+4,4 · 28 işlem |
| `supertrend` (çarpan 3) | %+23,0 · 24 işlem | %+25,0 · 16 işlem |
| `supertrend_sik` (çarpan 2) | %+25,0 · 40 işlem | %+22,5 · 28 işlem |
| `sma_cross` (10/50) | %+54,8 · 16 işlem | %+28,8 · 14 işlem |
| Al-tut / XU100 | %+76,4 / %+24,5 | %+43,9 / %+10,5 |

Kör test saatlik barlarda da çalışır (Yahoo saatlik veriyi son 729 gün için verir; eğitim ve test bu aralığa sığmalıdır). Aynı hisselerde saatlik barlar işlem sayısını 4-10 katına çıkarır ama işlem başına getiri maliyetin (gidiş-dönüş yaklaşık %0,5) altına iner: `supertrend_sik` 2025-04 → 2025-10'da günlükte %+9,3 · 20 işlem, saatlikte %+12,9 · 121 işlem; 2025-10 → 2026-10'da günlükte %+22,5 · 28 işlem, saatlikte %-5,0 · 268 işlem. Saatlikte standart `supertrend` (çarpan 3) daha iyidir (%+17,3 · 72 ve %+7,9 · 150 işlem).

**Daha önce denenmemiş hisselerde son 3 ay (2026-07-03 → 2026-10-02, saatlik).** Bu dönemde piyasa düştü: XU100 %-15,1 değişti, kaybın büyük kısmı eylüldeydi (%-17).
- **MGROS:** `supertrend_sik` sinyalleri %-6,5 · 13 işlem; yapay zeka ve karar günlüğüyle %-0,5 · 7 işlem; al-tut %-18,7.
- **8 hisselik sepet** (ULKER, TAVHL, DOAS, CCOLA, AEFES, MAVI, CIMSA, TTKOM): sinyaller %-8,4 · 104 işlem; yapay zeka ve günlükle %-2,9 · 16 işlem, zamanın %6'sında piyasada; al-tut %-11,3.
- **Diğer kütüphane scriptleri** saatlikte ve günlükte de kaybettirdi.
- **Yapay zekanın farkı** çoğunlukla girişleri reddedip nakitte kalmasından geldi. Aldığı işlemlerin ortalaması da negatifti.
- **Aynı sepette aylık rotasyon** (saatlik emirlerle, `autorotate.replay`): 6 ay ve en iyi 3 ile %-9,2, eşit ağırlıkla %-11,7, 9 ay ve en iyi 3 ile %-14,4.
- **Rotasyon için bu süre kısa:** üç ayda yalnızca 3-4 karar var. XU100 filtresi ancak 1 Ekim kararında nakde geçti.

Trendi yeniden girişlerle ya da sıkı stoplarla parçalamak işlem sayısını artırır ama maliyet ve testere zararıyla getiriyi düşürür; ortalama uzunlukları gibi parametrelerde bir yılın en iyisi diğer yılın en kötülerinden olabilir. Bu yüzden iki dönemde de tutarlı kalan hassas Supertrend seçildi. Ölçümler yükselen bir piyasaya aittir; düşen piyasada trend takibi daha az işlem ve farklı sonuç verir.

**Öğrenen kör test.** Kör test formundaki **Öğrenme** seçeneği:
- **Karar günlüğü:** hisseler ortak bir tarih takviminde birlikte ilerler; her kararın sonucu yalnızca gerçekleştiği bardan sonraki kararlara gösterilir. AL kararının sonucu açtığı işlemdir; reddedilen girişin sonucu aynı sinyalin yapay zekasız işlemidir (kaçırılan ya da kaçınılan getiri); SAT/TUT kararları sonraki 10 barla ölçülür. Görünüme bir özet (alınan girişlerin kazançlı oranı, reddedilenlerin kaçının kâr ettireceği), son 6 sonuç ve sınanmış kurallar eklenir. Her 12 yeni sonuçta asistan modeli günlüğü ve geçmiş sinyallerin ölçüm dilimlerini okuyup en fazla 5 kural önerir (ör. "SMA200'e uzaklık % > 15 ise reddet"). Kural ancak stratejinin karar anına kadar kapanmış geçmiş sinyallerinde en az 10 örnekle iddiasını tutturursa kullanılır; tutmayanlar atılır ve koça gösterilir. Hisseler harfle anılır, tarih yoktur. Test bitişini ileri almak önceki hiçbir kararın görünümünü değiştirmez (testle doğrulanır).
- **Günlük + script turları:** test dönemi 2–4 eşit pencereye bölünür. Her pencere sonunda açık pozisyonlar kapanır ("tur sonu"), sermaye sonraki pencereye taşınır ve script yazarı scripti pencerenin anonim raporu (yapay zeka, sinyaller, al-tut, XU100, hisse bazında sonuçlar, günlük özeti, ders notları) ve pencere sonuna kadar güncellenmiş sinyal araştırmasıyla geliştirir. Hedef: getiride XU100'ü ve yapay zekasız sonucu geçmek, her hissede pencere başına en az 1 işlem, al-tuttan küçük düşüş. Yeni script `<script>_t2`, `_t3`… olarak kaydedilir ve yalnızca sonraki pencerede kullanılır; doğrulanamazsa eski script sürer. Sonuçta tur tablosu ve ilk scriptin revizyonsuz tüm dönem sonucu da gösterilir.

**Karar kanıtı.** Yapay zekalı ve istatistik filtreli kör testte her karar görünümü şunları da içerir:
- **`gunluk`:** saatlik kararlarda hissenin günlük resmi (5/20/60 gün getiri, günlük SMA50/200'e uzaklık, RSI). Önceki günün kapanışına kadardır, son değer anlık fiyattır.
- **`piyasa`:** XU100'ün 20 günlük değişimi, SMA50'ye uzaklığı ve SMA200'ün üstünde olup olmadığı, ayrıca 20 büyük hissenin yüzde kaçının 50 günlük ortalamasının üstünde olduğu (genişlik).
- **`benzer_gecmis_sinyaller`:** giriş kararlarında, stratejinin bu ana en çok benzeyen geçmiş sinyallerinin maliyet sonrası sonucu ve tüm geçmiş sinyallerin sonucu. Kaynak, test hisseleri ve 20 büyük hissedeki strateji işlemleridir; bir işlem ancak karar anından önce kapanmışsa sayılır.
- **`benzer_gecmis_cikislar`:** çıkış kararlarında, benzer geçmiş çıkış sinyallerinden sonra pozisyonu 10 bar (saatlikte 45 bar) daha tutmanın sonucu. Koç giriş kurallarının ("reddet", "uygula") yanında çıkış kuralları ("tut", "sat") da önerebilir; çıkış kuralları bu örneklerde sınanır.

Genel göstergeler stratejiden ayrı adlandırılır (`supertrend_genel` çarpan 3'tür), çünkü model hızlı Supertrend'in erken dönüşünü yavaş olanla çelişki sanıp reddediyordu.

**İstatistik filtresi** (`mode: "stats"`) yapay zekasız bir ölçüttür. Giriş sinyalini, benzer geçmiş sinyaller maliyet sonrası ortalamada kazandırdıysa alır. Çıkış sinyalinde ise benzer geçmiş çıkışlardan sonra tutmak ortalamada kazandırdıysa pozisyonu tutar.

**Karar karşılaştırması:** her sonuçta her giriş kararı, sinyallerin aynı açılışta açtığı işlemle ölçülür. Gösterilenler:
- alınan ve reddedilen sinyallerin kazançlı oranı ve ortalaması
- aynı sayıda rastgele sinyal seçmenin beklenen sonucu ve rastgele seçimleri geçme oranı
- doğru karar oranı ile "hepsini reddet" ölçütü

2019–2024-09 arasında 20 büyük hissedeki 670 `supertrend_sik` işleminde yapılan ileriye yürüyen sınamada, bu ölçümlerden kurulan filtrelerin (benzer sinyal, ağırlıklı benzer sinyal, ridge regresyon) hiçbiri "her sinyali al" politikasını toplamda geçemedi; reddedilen işlemler de ortalamada kazandırıyordu. Bu yüzden talimat, ancak benzer sinyaller ortalamada zarar ettiriyorsa reddetmeyi söyler. Aynı dönemde `supertrend_sik`'in 731 çıkış sinyalinden sonraki 10 günde fiyat ortalama %+2,7 yükseldi (%58,5'inde) ve bu her yıl artıydı; hızlı çıkışlar çoğu zaman erkendi.

**Kıyas (2026-10-03).** Ölçüm `supertrend_sik` ile, karar günlüğü açık yapıldı. A ve B 20 büyük hissede günlük bar; S 8 hisselik sepette son 3 ayın saatlik barı:

| Karar katmanı | A (2024-10 → 2025-10) | B (2025-10 → 2026-10) | S (2026-07 → 2026-10) |
|---|---|---|---|
| Sadece sinyaller | %+4,4 | %+5,7 | %−8,4 |
| Eski tasarım, Gemini Flash | %+14,5 | %+5,4 | %−2,9 |
| Eski tasarım, Claude Haiku 4.5 | %+6,9 | %−3,4 | %−1,5 |
| Yeni tasarım, Claude Haiku 4.5 | %+11,3 | %+1,2 | %−1,0 |
| Eski tasarım, Claude Sonnet 5.5 | %+10,3 | %+1,3 | %−2,2 |
| Yeni tasarım, Claude Sonnet 5.5 | %+17,7 | %+0,2 | %−0,7 |
| İstatistik filtresi | %+16,8 | %+2,0 | %−4,0 |
| **Hepsini al + çıkış kararı, Sonnet 5.5** | %+12,9 | **%+15,2** | %−6,2 |
| **Hepsini al + çıkış kararı, istatistik filtresi** | **%+19,7** | %+13,5 | %−9,9 |
| Al-tut / XU100 | %+23,4 / %+24,5 | %+9,1 / %+10,7 | %−11,3 / %−15,1 |

- **Tasarım:** aynı modelle yeni tasarım altı karşılaştırmanın beşinde öndeydi.
- **A dönemi:** kazanç çoğunlukla çıkış sinyallerine rağmen tutmaktan geldi. Sonnet'in reddettiği 6 giriş, alınsaydı ortalama %−5,2 getirecekti.
- **B dönemi:** benzer-sinyal kanıtı ters çalıştı. Kanıtın zararda gösterdiği 42 sinyal gerçekte ortalama %+5,7 kazandırdı. Bu yüzden girişleri de eleyen bütün kollar sinyallerin gerisinde kaldı.
- **S dönemi:** düşen piyasada az işlem yaparak kaybı küçülttüler.
- **Hepsini al (`entries: "take"`):** girişleri elemek yerine her giriş sinyali alındı. Hisse 200 günlük ortalamasının %35'ten fazla üstündeyse yarım boyut kullanıldı. Karar katmanı yalnızca çıkışlara karar verdi.
  - Yalnızca bu kollar iki yükselen yılda da kazandırdı.
  - B'de al-tutu ve XU100'ü geçtiler.
  - Sonnet ile Sharpe oranı üç dönemde de sinyallerin yaklaşık iki katı oldu.
  - Bedeli, düşen piyasada girişleri de almak: S'de kayıp sinyallere yakın ama al-tuttan ve XU100'den az.
  - Bu tasarımı 2019–2024/09 verisi destekliyor: o dönemde hiçbir giriş filtresi "hepsini al"ı geçemedi; yarım boyut eşiği de bu dönemden seçildi.
- **Piyasa rejimi kuralı (denendi, bırakıldı):** 2019–2024/09 verisinde düşüş rejimlerinde gelen girişler bile ortalamada kazandırdı:
  - XU100 50 günlük ortalamasının altındayken %+3,3, 200 günlük ortalamasının altındayken %+10,4.
  - Girişleri atlamak toplamı belirgin düşürdü; yarım boyut risk başına getiriyi en fazla %3 iyileştirdi.
  - Bu dönemin en iyi adayı "XU100 20 günde %−3'ten fazla düştüyse yarım boyut"tu. İstatistik filtresiyle %+15,5 / %+9,0 / %−8,9 verdi: kuralsız sürümden A'da 4, B'de 4,5 puan düşük, S'de yalnızca 1 puan iyi.
- **Kanıt isabeti kapısı (denendi, bırakıldı):** benzer-sinyal kanıtını yalnızca son 30 sinyalde isabetliyse göstermek A'da kazancı yarıya indirdi, B'yi düzeltmedi (Sonnet: %+9,2 ve %+0,6). Kanıtın son isabeti sonraki isabetini öngörmüyor.

**Başka piyasalarda deney** (`scripts/market_experiment.py`, ürün özelliği değil):

- **Kurulum:** aynı kör test ve tasarım (`supertrend_sik`, BIST verisiyle seçilmiş kurallar) ABD'nin ve kripto paraların bugünkü büyüklerinde denendi.
  - ABD: A ve B'de 20 büyük hisse, S'de ayrı 8 hisse. Komisyon yok, kayma %0,05; ölçüt SPY.
  - Kripto: A ve B'de 15 büyük coin, S'de 8 büyük coin. %0,1 komisyon ve %0,1 kayma; ölçüt BTC.
- **Dönemler:** A ve B günlük bar, S son 3 ayın saatlik barı.

| | ABD A | ABD B | ABD S | Kripto A | Kripto B | Kripto S |
|---|---|---|---|---|---|---|
| Sadece sinyaller | %+5,0 | %+2,6 | %−14,3 | %+62,9 | %−18,8 | %+12,7 |
| İstatistik filtresi, girişleri eleyerek | %+5,7 | %+3,2 | %−8,7 | %+96,4 | %−32,7 | %+10,7 |
| İstatistik filtresi, hepsini al | %+7,5 | %+7,5 | %−14,5 | %+93,0 | %−30,1 | %+25,8 |
| Sonnet 5.5, hepsini al | %+0,3 | %+3,8 | %−14,7 | %+89,8 | %−20,7 | %+22,1 |
| Momentum rotasyonu 9 ay / 6 ay | %+17,7 / %+14,9 | %+2,4 / %+0,1 | — | %+81,8 / %+121,1 | %−38,3 / %−44,5 | — |
| Eşit ağırlıklı al-tut | %+17,8 | %+10,4 | %−13,2 | %+113,6 | %−46,3 | %+44,6 |
| Ölçüt (SPY / BTC) | %+17,5 | %+15,0 | %+2,6 | %+95,7 | %−30,0 | %+37,3 |

- **ABD:** bütün kollar al-tutun ve SPY'ın çok gerisinde kaldı; momentum rotasyonu eşit ağırlıklı sepeti de geçemedi. Bu strateji ve tasarım ABD büyük hisselerine taşınmıyor.
- **Kripto:** karar katmanı sinyallere belirgin değer kattı.
  - Sonnet çıkışlarda yükselişte tutup düşüşte sattı. Sharpe oranı A'da 1,48 (al-tut 1,42, sinyaller 1,27) oldu.
  - En büyük düşüş A'da %−42 (al-tut %−52), B'de %−36 (al-tut %−64).
  - İstatistik filtresinin giriş seçimi kripto'da rastgele seçimlerin %99, %64 ve %96'sını geçti; ABD'de %17, %16 ve %45'ini.
- **Genel:** "hepsini al" ve çıkışta tutmak sistemi al-tuta yaklaştırıyor: yükselişte kazancı artırıyor, düşüşte kaybı. Sonnet düşen piyasalarda (kripto B, BIST S) bunu kurala göre daha iyi dengeledi.
- **Uyarı:** sepetler bugünün büyüklerinden seçildi (hayatta kalma yanlılığı) ve dönemler kısa.

**Kripto, yapay zekasız stratejiler** (`scripts/crypto/`, deney):

Kütüphanedeki 8 strateji, istatistik filtresi ve momentum rotasyonu 15 büyük coinde (BTC, ETH, BNB, SOL, XRP, ADA, DOGE, TRX, AVAX, LINK, DOT, LTC, BCH, XLM, ATOM) takvim yılı pencerelerinde (2021 → 2026 Ekim) çalıştırıldı.
- Maliyet: %0,1 komisyon ve %0,1 kayma.
- Her yıl 100.000 $ ile başlanıp kazanç ertesi yıla taşındı.
- Ayar ve filtreler yalnızca 2021–2023'te seçildi; 2024–2026 dokunulmadan sınandı.

| 100.000 $ ile | 2021–23 (seçim) | 2024–26 (sınama) | 2021 → 2026 | En büyük düşüş |
|---|---|---|---|---|
| Eşit ağırlıklı al-tut | 546.657 $ | 116.585 $ | 637.322 $ | %−70 |
| BTC | 139.067 $ | 186.695 $ | 259.630 $ | — |
| supertrend_sik | 409.116 $ | 130.047 $ | 532.043 $ | %−44 |
| **supertrend_sik + BTC 50 günlük filtresi** | 587.002 $ | **201.717 $** | 1.184.082 $ | **%−28** |
| donchian + BTC 50 günlük filtresi | 400.643 $ | 136.961 $ | 548.725 $ | %−37 |
| SMA kesişimi + BTC 50 günlük filtresi | 287.077 $ | 147.071 $ | 422.208 $ | %−42 |
| Rotasyon 3 ay / en iyi 3 | 366.158 $ | 191.865 $ | 702.531 $ | %−80 |
| **Rotasyon 3 ay / en iyi 3 + BTC 200 günlük filtresi** | 777.383 $ | **201.334 $** | 1.565.135 $ | %−45 |

- **BTC filtresi işe yarayan geliştirme:**
  - Trend stratejilerinde "BTC 50 günlük ortalamasının üstündeyse giriş, çıkış sinyalde" kuralı üç stratejinin üçünü de hem seçim hem sınama döneminde iyileştirdi.
  - Rotasyonda BTC 200 günlük ortalamasının altında nakde geçmek 2022 çöküşünü atlattı.
- **Çıkışta tutmak (BIST'teki "hepsini al" katmanı) kriptoda zarar verdi:** çöküşlerde pozisyon taşıdı, düşüş %−60 ile %−72'ye çıktı.
- **Parametre ayarı** varsayılanlara göre küçük fark yarattı.
- **Dönüş stratejileri** (RSI, Bollinger) günlükte zayıf kaldı.
- **Saatlik barlar (2025-04 → 2026-10, 8 coin, 3 aylık pencereler):** bütün stratejiler kaybetti. En iyisi donchian kırılımı 82.613 $; al-tut 88.478 $, BTC 101.490 $.
- **Scalping** (25 Ağustos – 2 Ekim 2026, 6 coin, 5 ve 15 dakikalık bar):
  - Sonucu ücret belirledi. 5 dakikalık Bollinger dönüşü komisyonsuz 113.980 $, %0,02 ücretle 99.860 $, %0,1 ücretle 70.200 $ (1.322 işlem).
  - İlk 20 günde ayarlanıp son 19 günde sınanan en iyi scalping, 15 dakikalık RSI dönüşüydü (26 işlem): %0,1 ücretle 105.750 $. Aynı dönemde al-tut 108.860 $.
  - Hiçbir scalping stratejisi elde tutmayı geçemedi.
- **Uyarı:** coinler bugünün büyüklerinden seçildi (2022'de çöken LUNA ve FTT gibi coinler listede yok); bu, al-tutu ve rotasyonu iyimser gösterir.

**Coin başına uyarlama, ileriye yürüyerek** (`scripts/crypto/adaptive.py`, `ensemble.py`, `momentum_trend.py`):

Her çeyrek başında her coin için 46 aday (supertrend, SMA kesişimi, Donchian ve MACD trend ayarları, çoklu trend, elde tutma) o coinin son 12 ayında puanlandı ve en iyisi sonraki çeyrekte işlem yaptı. Portföy her çeyrek coinlere eşit dağıtıldı. 2022 başından 2026 Ekim'e, 100.000 $ ile:

| Yöntem | Bitiş | 2022 | 2025 |
|---|---|---|---|
| Al-tut / BTC | 90.756 $ / 182.518 $ | %−70 / %−64 | %−32 / %−6 |
| supertrend_sik | 186.371 $ | %−36 | %−38 |
| **supertrend_sik + BTC 50 günlük filtresi** | **388.583 $** | **%−3** | %−17 |
| Coin başına uyarlanmış, BTC filtreli (en iyi varyant) | 252.290 $ | %−2 | %−20 |
| Coin başına uyarlanmış, filtresiz | 185.821 $ | %−23 | %−28 |
| 7 stratejili topluluk, BTC filtreli | 151.706 $ | %−30 | %−19 |
| Aynı kural, son 90 günün en güçlü 5 coininde | 411.746 $ | %−13 | %−19 |
| Aynı kural, en güçlü 3 coinde | 666.868 $ | %−16 | %−14 |

- **Coin başına uyarlama** (getiriye ya da getiri/düşüşe göre seçim, elde tutmalı ya da elde tutmasız), herkese aynı uygulanan basit kuraldan kötü çıktı: bir coinde son 12 ayda en iyi çalışan ayar sonraki çeyreğe taşınmıyor.
- **Getiriyi artıran,** coin başına ayar gerektirmeyen iki kenarın birleşimi oldu: BTC trend filtresi ve momentumla coin seçimi. En güçlü 3 coin daha yoğun ve oynak; 2024'teki birkaç ralliye dayanıyor.
- **Son 3 ay** (Temmuz – 3 Ekim 2026) kesintisiz bir yükselişti: al-tut 147.792 $, BTC 144.667 $, stratejiler 108–121 bin $. Zamanlama stratejileri çöküşten kaçınmak için yükselişin bir kısmını kaçırır (2022: %−3 ve %−70).

**Ek veri kaynakları (2026-10-03 incelemesi).**

- **Bilanço:**
  - Yahoo yalnızca son 5 çeyreği ve 4 yılı veriyor. İş Yatırım'ın mali tablo servisi ise 2017'den beri çeyreklik tabloları veriyor. Bankalar ayrı formatta (UFRS_K).
  - Tablolar dönem sonundan 75 gün (yıl sonunda 100 gün) sonra bilinir sayılırsa ileriye bakmadan kullanılabilir.
  - 2019–2024/09'da 16 sanayi ve holding şirketinin `supertrend_sik` sinyallerinde kâr büyümesi, satış büyümesi, özkaynak kârlılığı, marj, borç/özkaynak, nakit kalitesi, kazanç getirisi ve defter/piyasa ile sinyal sonucu arasındaki sıra korelasyonları ±0,1'in altında kaldı ve çoğu yıldan yıla yön değiştirdi.
  - Bilanço bu yüzden işlem başına karar için değil, aylık hisse seçiminde (momentum rotasyonuna değer/kalite eğilimi olarak) denenmeye daha uygun.
  - 2023 sonundan beri enflasyon muhasebesi (TMS 29) uygulandığı için önceki ve sonraki tablolar doğrudan karşılaştırılamaz.
- **Derinlik (emir defteri):** Yahoo vermiyor. Geçmiş kademe verisi ücretsiz değil; gerçek zamanlı derinlik Borsa İstanbul lisanslı veri dağıtıcılarından alınır. Kör testte yerine günlük TL hacmi, sıfır hacimli gün oranı ve Amihud likidite ölçüsü kullanılıyor.
- **Haberler:**
  - Borsa İstanbul için asıl kaynak KAP bildirimleri. Şirket listesi alınabiliyor ama geçmiş bildirim sorgusu zaman aşımına uğradı; Yahoo THYAO için haber döndürmedi.
  - Haber metni hisseyi ve tarihi ele vereceği için kör teste ancak anonim olay özellikleri olarak girebilir: bedelsiz, geri alım, temettü, finansal rapor ya da özel durum açıklaması gibi.
  - Metin olarak yalnızca canlı (sanal hesap) kararlarda kullanılabilir.

**Geliştirmede Claude aboneliğiyle karar** (`marketalyzer/ai/claude_cli.py`): `claude_cli.decider()` (Haiku; `decider("claude-sonnet-5-5")` ile Sonnet) ve `claude_cli.coach()` (Sonnet), `blind.run_blind(config, decider, coach=coach)` ile kullanılır. Her istek `claude -p` ile çalışır:
- araç, MCP, kayıtlı oturum ve düşünme kapalıdır;
- yalnızca proje ayarları yüklenir; kullanıcının Claude Code ayarlarındaki anahtar ve ağ geçidi kullanılmaz ve değiştirilmez;
- API kredisi harcanmaz, planın kullanım sınırı geçerlidir.

Komut satırının bir kez `claude auth login` ile Claude hesabına bağlanması gerekir. Giriş yoksa ya da plan sınırı dolarsa kör test açık bir mesajla durur.

Her yapay zekalı ve istatistik filtreli kör test `~/.local/share/marketalyzer/ai/lab/runs/` altına kaydedilir (`/api/lab/runs`). Canlı karar, aynı scriptle öğrenen en son kör testin kurallarını (ya da ders notlarını) kullanır.

**Hızlı karar modeli.** Kararlar küçük ve hızlı modellerle verilir; istek OpenRouter'da en düşük gecikmeli sağlayıcıya yönlendirilir (`provider.sort = latency`), sıcaklık 0'dır ve yanıt tek satır JSON'dur (`{"karar", "guven", "gerekce"}`). Ön ayarlar her ailenin OpenRouter'daki en yeni sürümüne çözülür:

| Ön ayar | Aile | Not |
|---|---|---|
| `claude-haiku` (varsayılan) | Anthropic Claude Haiku | JSON biçimine sadık, tutarlı |
| `gemini-flash` | Google Gemini Flash | akıl yürütme düşük |
| `gemini-flash-lite` | Google Gemini Flash Lite | çok düşük gecikme ve maliyet |
| `gpt-mini` / `gpt-nano` | OpenAI GPT mini / nano | akıl yürütme düşük |
| `deepseek-flash` | DeepSeek Flash | en ucuz, akıl yürütme kapalı |
| `qwen-flash` | Qwen Flash | ucuz alternatif |

Varsayılan **Ayarlar → Karar modeli** bölümünden değiştirilir; aynı yerde **Hız testi** her ön ayara aynı örnek kararı sorup ortalama süreyi, örnek kararı ve maliyeti gösterir. Herhangi bir OpenRouter model kimliği de yazılabilir ya da `MARKETALYZER_DECISION_MODEL` ile sabitlenebilir. Bir model JSON modu veya akıl yürütme ayarını reddederse istek bir kez sade olarak tekrarlanır. Kararlar `~/.local/share/marketalyzer/ai/decisions.sqlite` dosyasında model ve görünüm anahtarıyla saklanır: aynı test tekrarlandığında aynı kararlar ücretsiz ve anında gelir (kapatılabilir).

Tahmin düğmesi testten önce en fazla kaç karar gerektiğini, modelin fiyatına göre yaklaşık maliyeti ve süreyi gösterir. Asistan da `signal_study` aracıyla aynı araştırmayı sohbette kullanabilir.

## Momentum rotasyonu ve araştırma notları

**AI Strateji → 5 · Momentum rotasyonu** (`marketalyzer/rotation.py`, `POST /api/lab/rotation`): her ayın son kapanışında hisse listesi (varsayılan: BIST'in 20 büyük şirketi) son 6, 9 ya da 12 aydaki getiriye göre sıralanır (son ay hariç); en güçlü 3, 5 ya da 7 hisse eşit paylarla ertesi gün açılışta alınır, listeden düşenler satılır, kalanlara dokunulmaz. Sıralama yalnızca karar kapanışına kadarki fiyatları kullanır; işlemler tam lot, BIST komisyonu, BSMV ve kaymayla yapılır. İsteğe bağlı filtreler: 200 günlük ortalamasının altındaki hisseyi almamak (o pay nakitte kalır) ve XU100 200 günlük ortalamasının altındayken tamamen nakde geçmek. Sonuçta eşit ağırlıklı al-tut ve XU100 ile karşılaştırma, ay ay değişimler ve bu ay tutulacak hisseler (emir fişiyle) gösterilir. Bugünün büyük şirketlerinden seçilen liste hayatta kalma yanlılığı taşır: dönem içinde endeksten düşenler yoktur.

**Sanal hesapta otomatik rotasyon** (`marketalyzer/paper/autorotate.py`, `marketalyzer-rotation`): rotasyon kartındaki "Bu ayarlarla otomatik uygula" düğmesi planı kaydeder ve hemen en son kapanışa göre alır. Sonra her ayın ilk işlem gününde önceki ayın son kapanışına göre yeniden sıralar: listeden düşenleri satar, satışlar gerçekleşince yenileri alır. Emirler piyasa emridir ve saatlik barların açılışında gerçekleşir. Rotasyon yalnızca kendi aldığı hisseleri satar; hesaptaki diğer pozisyonlara dokunmaz. Plan ve geçmişi `~/.local/share/marketalyzer/rotation/<hesap>.json` dosyasındadır. Planı ilerletmek için hafta içi işlem saatlerinde sık aralıkla çalıştırın:

```bash
marketalyzer-rotation step --account web
```

VPS'te bunu `marketalyzer-rotation.timer` (hafta içi 09:00-19:45, 15 dakikada bir, İstanbul saati) yapar. Aynı mantık `autorotate.replay` ile geçmiş saatlik barlarda canlıymış gibi oynatılabilir.

Neden bu yöntem: 2021-10 → 2026-09 arasındaki beş ayrı yılda, yapay zekasız ve aynı maliyetlerle yapılan ölçümler (araştırma düzeneğinde, aylık eşit ağırlığa dönerek):

| 20 büyük hisse | Ortanca yıl | En kötü yıl | XU100'ü geçtiği yıl |
|---|---|---|---|
| Rotasyon, 6 ay momentum, en iyi 5 | %31,5 | %19,5 | 4/5 |
| Rotasyon, 9 ay momentum, en iyi 5 | %57,5 | %19,5 | 4/5 |
| Hisse bazında en iyi trend (çoklu ufuk) | %23,6 | %-0,8 | 1/5 |
| `supertrend_sik` hisse bazında | %15,7 | %3,5 | 1/5 |

Momentum ufku 6-9 ay ve tutulan hisse sayısı 3-7 arasında sonuç kararlıdır; 4 ay belirgin biçimde zayıftır. Hisse bazında trend stratejileri senin seçtiğin 5 güçlü hissede iyi görünürken 20 hissede zayıfladı: başarının önemli kısmı hisse seçiminden geliyordu. Hiçbir yöntem bu yıllarda TL bazında al-tutu düzenli geçemedi (2021-23'te XU100 %127 ve %146 yükseldi).

Literatürden yararlanılanlar:

- Hisseler arası momentum (geçmiş 6-12 ayın kazananlarını tutmak) en sağlam belgelenmiş etkilerden biridir; Borsa İstanbul'da 2005'ten beri anlamlı bir faktördür, 1990'larda ise tersine (contrarian) çalışmıştı ([Factor investing in the Turkish equity market](https://www.sciencedirect.com/science/article/pii/S2214845026001043), [Bildik & Gülay](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=302299)).
- BIST100 endeksinin kendisinde (2000-2025, aylık) ekonomik olarak anlamlı zaman serisi momentumu bulunmamış, ortalamaya dönüş baskındır ([Trends in Business and Economics](https://dergipark.org.tr/en/pub/trendbusecon/article/1815580)); bu yüzden endeks zamanlaması isteğe bağlı bir filtre olarak bırakıldı.
- Trend sinyallerinde tek bir ufuk yerine birkaç ufku birlikte kullanmak zamanlama belirsizliğine karşı sağlamlık verir ([Hurst, Ooi & Pedersen](https://www.trendfollowing.com/whitepaper/Century_Evidence_Trend_Following.pdf)); kütüphanedeki `coklu_trend` 20, 60 ve 200 günlük ortalamaların oylamasıdır.
- Oynaklığa göre pozisyon küçültmek Sharpe oranını ve kuyruk riskini iyileştirir ([Moreira & Muir](https://www.nber.org/system/files/working_papers/w22208/w22208.pdf), [Harvey ve diğ.](https://people.duke.edu/~charvey/Research/Published_Papers/P135_The_impact_of.pdf)); zaman serisi momentumunun getirisinin önemli kısmı bu ölçeklemeden gelir ([Kim ve diğ.](https://www.researchgate.net/publication/303846490_Time_series_momentum_and_volatility_scaling)).
- Çok sayıda varyant denenip en iyisi seçildiğinde sonuç şişer; az deneme ve komşu ayarlarda kararlılık aranmalıdır ([Deflated Sharpe Ratio](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf)).
- Dil modelleri eğitim dönemlerindeki fiyatları ezberleyebilir; yapay zeka stratejilerinin çoğu gerçek test dışı veride al-tutu geçemiyor ([Look-ahead bias in LLM forecasts](https://arxiv.org/pdf/2512.23847)). Kör testteki hisse adı, tarih ve fiyat seviyesi gizleme bu yüzdendir.

## Kripto sanal hesap (7/24)

**AI Strateji → 6 · Kripto sanal hesap** (`marketalyzer/crypto/`, `marketalyzer-crypto`, `GET /api/crypto`, `POST /api/crypto/step`): kripto araştırmasında öne çıkan yapay zekasız kural USD bazlı, kesirli miktarlı ayrı bir sanal hesapta sürekli çalışır. Her çeyrek başında 15 büyük coin son 90 günlük getiriye göre sıralanır, en güçlü 5'i seçilir (bir yıldan kısa geçmişi olan coin seçilmez). Seçilen coinde supertrend (çarpan 2, ATR 10) yukarı dönünce ve BTC 50 günlük ortalamasının üstündeyken özsermayenin 1/5'iyle alınır; supertrend aşağı dönünce ya da coin seçimden düşünce satılır. Kararlar UTC'ye göre kapanmış günlük barlarla verilir, işlem en son saatlik kapanıştan komisyon %0,1 ve kayma %0,1 ile yapılır. 2022 → 2026-10 testinde 100 000 $ bu kuralla 388 600 $ olurken eşit ağırlıklı al-tut 90 800 $, BTC 182 500 $ oldu.

```bash
marketalyzer-crypto init --cash 100000   # hesabı açar (var olanın üzerine yazmaz)
marketalyzer-crypto step                 # kapanmış yeni günleri işler; aynı saatte tekrar çalışırsa bir şey yapmaz
marketalyzer-crypto status
```

`step` kaçırılan günleri sırayla işler (en fazla 14 gün). Hesap `~/.local/share/marketalyzer/crypto/paper.sqlite` dosyasındadır. VPS'te `marketalyzer-crypto.timer` her saatin 10. dakikasında (UTC, 7/24) bir adım çalıştırır.

**NeuralEngine: öğrenen uzman seçimi** (`marketalyzer/crypto/neural.py`, `scripts/crypto/neural_engine.py`). Altı sınanmış kural, yani uzman, gölgede kendi defterini işletir: en güçlü 5 / en güçlü 3 / 15 coin + supertrend + BTC filtresi, 3 aylık rotasyon + BTC 200 günlük filtre, SMA 20/100 + BTC filtresi ve nakit. Motor her gün uzmanların gerçekleşen log getirisine göre ağırlıklarını çarpımsal olarak günceller (Hedge). Bunu piyasa durumuna göre ayrı ayrı yapar: BTC'nin 50/200 günlük ortalamalara göre konumu, genişlik ya da oynaklık. Eski sonuçlar unutulur ve her uzman bir taban ağırlık korur. Sermaye ağırlıklarla uzmanların pozisyonlarına paylaştırılır. Ayarlar yalnızca 2021-23'e göre seçildi; motor 2021'den kesintisiz çalıştı ve 2024 → 2026-10 dönemine dokunulmadı.

| 100 000 $'dan, 2024 → 2026-10 | Para | En büyük düşüş | Sharpe |
|---|---|---|---|
| Sabit kural (sanal hesap: en güçlü 5) | 246 900 $ | %-41 | 0,95 |
| En güçlü 3 + aynı kural | 315 400 $ | %-38 | 1,04 |
| NeuralEngine (2021-23 Sharpe'ına göre seçilen ayar) | 190 200 $ | %-30 | 0,80 |
| NeuralEngine (2021-23 parasına göre seçilen ayar) | 163 200 $ | %-43 | 0,45 |
| Uzmanların eşit karışımı (öğrenmesiz) | 194 400 $ | %-28 | 0,84 |
| Eşit ağırlıklı al-tut | 120 700 $ | %-70 | 0,10 |
| BTC | 199 900 $ | %-53 | 0,53 |

Denenen 96 ayarın hiçbiri test döneminde sabit kuralı geçemedi; ortanca 192 500 $ ile eşit karışımın düzeyinde kaldı. Hızlı öğrenen ayarlar 2021'in kazananlarını kovaladı: eğitimde 1,8 M $ yaptılar, testte geriledi. Uzmanların günlük getiri farkı çoğunlukla gürültü olduğundan öğrenilen ağırlıklar ya eşit karışıma yaklaşıyor ya da gürültüyü izliyor. Bu, tahmin birleştirme literatüründeki bilinen sonuçtur: öğrenilen ağırlıklar test dışı veride çoğu zaman eşit ağırlığı geçemez (Smith & Wallis, 2009, *A Simple Explanation of the Forecast Combination Puzzle*). Hedge'in garantisi de zaten en iyi tek uzmana yaklaşmaktır, onu geçmek değildir. Motor tasarımdaki eşiği geçemediği için gölge moda alınmadı.

**İkinci hesap:** "En güçlü 3" (`marketalyzer-crypto init --account top3 --top 3`) ana hesabın yanında aynı kuralla çalışır. Her hesap kuralını kendi dosyasında tutar. `marketalyzer-crypto step` bütün hesapları ilerletir ve her coinin verisini bir kez indirir. Kart hesaplar arasında geçiş yapar ve özsermaye eğrilerini üst üste çizer.

**Kaldıraç denemesi** (`marketalyzer/crypto/leverage.py`, `scripts/crypto/leverage_test.py`): giriş ve çıkışlar sanal hesaplarla aynıdır, pozisyonlar izole marjinli sürekli vadeli kontratlardır. Varsayımlar:

- %0,05 taker komisyonu ve %0,1 kayma.
- Uzun pozisyon her gün pozisyon değeri üzerinden fonlama öder: yıllık %10, stres senaryosunda %20.
- Günün en düşük fiyatı marjini %1 bakım seviyesine indirirse pozisyon tasfiye edilir ve marjinin tamamı kaybedilir.

2024 → 2026-10, 100 000 $'dan, fonlama %10:

| Kural | Kaldıraç | Para | En büyük düşüş | Sharpe | Tasfiye |
|---|---|---|---|---|---|
| En güçlü 5 | spot (sanal hesap) | 247 100 $ | %-42 | 0,95 | 0 |
| En güçlü 5 | 1,5x | 288 500 $ | %-55 | 0,84 | 0 |
| En güçlü 5 | 2x | 229 000 $ | %-74 | 0,47 | 2 |
| En güçlü 5 | 3x | 286 100 $ | %-82 | 0,47 | 4 |
| En güçlü 3 | spot (sanal hesap) | 315 600 $ | %-38 | 1,04 | 0 |
| En güçlü 3 | 1,5x | 380 400 $ | %-50 | 0,94 | 0 |
| En güçlü 3 | 2x | 341 100 $ | %-68 | 0,66 | 1 |
| En güçlü 3 | 5x | 300 700 $ | %-93 | 0,31 | 7 |

Kaldıraç 2021-23 boğasında parayı katladı: en güçlü 5 ile 3x 5,7 M $ yaptı, spot 951 bin $. Ama her seviyede Sharpe düştü ve düşüş kaldıraçtan hızlı büyüdü. 10 Ekim 2025 çöküşünde 2x ve üstündeki hesaplarda LINK ve XLM pozisyonları tasfiye oldu. 5x'te 2021'de bile 16 tasfiye var. Fonlama %20 olunca her sonuç belirgin biçimde kötüleşiyor. Bu kuralda sabit kaldıraç riski ödülden hızlı artırıyor; 1,5x bile getiri başına daha fazla düşüş getiriyor.

### Araştırma turu: geriye dönük seçilmemiş evren (2026-10-04)

Plan `docs/superpowers/plans/2026-10-04-crypto-research-round.md` dosyasında. Literatür taramasının en önemli uyarısı şuydu: bugünün 15 büyük coinini geriye dönük seçmek, hayatta kalma yanlılığı yaratır. Eşit ağırlıklı ve kazananı kovalayan kurallarda bu yanlılık en büyüktür. Çöken coinler (LUNA, FTT) o dönem uygun olurdu, ama listede yoklar.

**Veri:**

- `marketalyzer/crypto/binance.py` Binance arşivindeki (data.binance.vision) 658 USDT çiftinin günlük mumlarını yerel önbelleğe indirir, kaldırılmış olanlar dahil. Henüz arşivlenmemiş günler REST'ten gelir. Anahtar gerekmez.
- `marketalyzer/crypto/universe.py` her ay başında o tarihte görülebilen evreni kurar:
  - En az 1 yıldır işlem gören ve 30 günlük ortanca hacmi en az 2 M $ olan coinlerden hacme göre ilk 20 alınır.
  - Sabit, sarılı, fiat ve kaldıraçlı tokenler hariçtir.
  - Bir haftadan uzun boşluğu olan sembol ayrı coin sayılır: eski LUNA ile LUNA 2.0 gibi.
- 2019-2026 arasında evrene 127 coin girdi. Kaldırılan coin, son kapanışından satılmış sayılır.

**Adaylar ve yargı yöntemi:**

- Adaylar ve parametreleri önceden sabitlendi (`marketalyzer/crypto/strategies.py`, `scripts/crypto/pit_research.py`).
- Seçim, 2020-23 Sharpe oranına göre yapıldı; test dönemi 2024 → 2026-10.
- Maliyet: taraf başına %0,1 komisyon ve %0,05 kayma, ayrıca 2 kat stres.
- İstatistik: deflated Sharpe (önceki turların ~150 denemesi dahil) ve CSCV ile aşırı uyum olasılığı (`marketalyzer/crypto/evaluate.py`).

| 100 000 $'dan | 2020-23 | Düşüş | Sharpe | 2024-26 | Düşüş | Sharpe | 2024-26, maliyet ×2 |
|---|---|---|---|---|---|---|---|
| Sanal hesabın kuralı (en güçlü 5) | 464 200 $ | %-27 | 1,02 | 240 500 $ | %-42 | 0,82 | 231 100 $ |
| En güçlü 3 | 276 400 $ | %-47 | 0,61 | 162 300 $ | %-46 | 0,42 | 156 100 $ |
| A25: kural + oynaklık %25 | 240 700 $ | %-15 | 1,34 | 178 000 $ | %-26 | 0,99 | 170 400 $ |
| A40: kural + oynaklık %40 | 410 200 $ | %-23 | 1,35 | 231 000 $ | %-39 | 0,92 | 216 200 $ |
| B25: sıralamasız supertrend + %25 | 327 800 $ | %-31 | 1,36 | 156 300 $ | %-29 | 0,74 | 144 700 $ |
| C25: Donchian topluluğu + %25 | 283 300 $ | %-38 | 0,96 | 110 000 $ | %-44 | 0,13 | 96 400 $ |
| E25: Donchian + BTC rejimi + %25 | 284 200 $ | %-24 | 1,43 | 136 300 $ | %-24 | 0,61 | 128 500 $ |
| F: haftalık 4 hafta momentum | 2 405 900 $ | %-53 | 1,13 | 100 300 $ | %-65 | 0,00 | 91 900 $ |
| Evrende eşit ağırlık (aylık) | 366 200 $ | %-88 | 0,38 | 76 800 $ | %-71 | -0,15 | 75 800 $ |
| BTC al-tut | 586 300 $ | %-77 | 0,64 | 200 400 $ | %-53 | 0,54 | 200 400 $ |

**Bulgular:**

- **Hayatta kalma yanlılığı büyüktü.** Sanal hesabın kuralı 2021-23'te dürüst evrende 332 700 $ yaptı; Yahoo'daki 15 coinlik listede 949 000 $ yapmıştı. "En güçlü 3"ün üstünlüğü tamamen bu yanlılıktan geliyordu. Dürüst evrende iki dönemde de en güçlü 5'in gerisinde kaldı.
- **Zamanlama şansı çok büyük.** Çeyreklik seçimin başlangıcı 0-12 hafta kaydırılınca:
  - 2024-26 parası 123 800 $ ile 378 900 $ arasında değişti.
  - 2020-26 parası 324 100 $ ile 1,92 M $ arasında değişti.
- **Dilimleme bu şansı daralttı** (`scripts/crypto/tranches.py`). Sermaye üç dilime bölünüp çeyrekleri birer ay arayla başlatılınca:
  - 2024-26 aralığı 171 000-254 900 $'a indi.
  - En büyük düşüş %-42'den yaklaşık %-33'e geriledi.
  - Ortanca para aynı kaldı.
- **Oynaklığa göre boyut iki dönemde de Sharpe'ı artırıp düşüşü azalttı:** A25 ve A40, kuralın kendisinden iyi. Getirisi ise daha düşük.
- **Önceden belirlenen kuralla seçilen E25 testte geride kaldı.** Düşüşü yarıya indirdi (%-24'e karşı %-42), ama Sharpe'ı 0,61'e karşı 0,82.
  - Deflated Sharpe 0,53 (önceki denemelerle 0,30) ve aşırı uyum olasılığı 0,49.
  - Benimseme şartı (DSR ≥ 0,95) sağlanmadı; **hiçbir aday istatistik olarak kanıtlanmadı**.
- **Fonlama kalabalığı katmanı benimsenmedi.** Eğitimde Sharpe'ı düşürdü (1,43'ten 1,36'ya); testte biraz iyi göründü.
- **Literatürün eşleşen bulguları:** Donchian topluluğu ve haftalık momentum test döneminde zayıf kaldı. Bu, 2020 sonrası kesitsel momentumun zayıfladığını gösteren çalışmalarla uyumlu.

**Uygulanan:** iki aday sanal hesapta ileriye dönük teste alındı (`marketalyzer/crypto/live.py`). Sanal hesap gerçek para içermez ve geleceğe dönük, tamamen test dışı veri üretir. Çalışan iki hesap karşılaştırma için sürüyor.

- **Gelişmiş (`gelismis`):**
  - Binance'teki o anki evren, çeyreklik en güçlü 5.
  - Üç dilim, çeyrekleri birer ay arayla başlar.
  - Supertrend ve BTC filtresi.
  - Oynaklık hedefi %40, toplam pozisyon en fazla 1x.
  - Dürüst testte en tutarlı çıkan iki iyileştirmeyi birleştirir.
- **Donchian (`donchian`):** önceden belirlenen kuralla seçilen aday, Donchian topluluğu + BTC rejimi + oynaklık %25.

```bash
marketalyzer-crypto init --account gelismis --signal supertrend --top 5 --tranches 3 --vol 0.4
marketalyzer-crypto init --account donchian --signal donchian --regime --vol 0.25
```

Bu hesaplar her ayın ilk kapanmış gününde evreni Binance'ten yeniden kurar. Her gün hedef ağırlıkları hesaplar ve hedefinden %25'ten fazla sapan pozisyonu dengeler.

**Uygulanmayanlar ve nedenleri** (araştırma raporuna göre):

- Korku ve açgözlülük endeksi: getiriyi izliyor, öncü değil.
- LSTM, Transformer ve gradyan artırma: kazanç küçük coinlerden geliyor, maliyet sonrası kalmıyor.
- Carry getirisi: 2025'te negatife döndü.
- Haftalık geri dönüş: yalnızca küçük coinlerde var.
- Yarılanma döngüsü: 4 olay, kanıt çelişkili.
- BTC hakimiyeti, haftanın günü etkileri.
- 1x üstü kaldıraç: kendi testimizde Sharpe'ı düşürdü.

## Web uygulaması ve Cloudflare tüneli

Masaüstünde pencere çerçeveli, kenar çubuklu bir uygulama; telefonda alt sekme çubuğu ve alttan açılan sayfalarla yerel uygulama hissi veren bir arayüz. Kenar çubuğundaki **Piyasa / Lab** anahtarı iki çalışma alanı arasında geçiş yapar:

- **Panel:** dot-matrix tahmini bakiye (TL/USD, gizlenebilir), günlük K/Z, pozisyon kartları (al/sat/analiz), yapay zeka kartı, özsermaye ile BIST 100 karşılaştırması, seans saatli hızlı emir kartı, tutar → lot hesaplayıcı.
- **Piyasa:** izleme listesi, mum/çizgi grafik, fiyat grafiğine ya da ayrı panellere eklenebilen script göstergeleri, teknik özet (RSI, stokastik, ADX, Bollinger %B, 52 hafta aralığı, hacim, hareketli ortalamalar, pivot destek/direnç).
- **Emirler / İşlem:** açık ve geçmiş emirler, pozisyonlar, dağılım, gerçekleşen işlemler, emir fişi, strateji ile işleme ve hesap ayarları. Kenar çubuğunda açık emir kartı ve seans yayı.
- **AI Strateji:** sinyal araştırması, yapay zekaya Pine Script yazdırma, kör backtest ve canlı karar (yukarıdaki bölüm).
- **Script editörü:** sözdizimi renklendirme, satır numaraları, otomatik tamamlama, anlık derleme ve hata satırı, parametre formu, konsol, fonksiyon başvurusu; grafikte çalıştırma, backtest, optimizasyon ve walk-forward.
- **Backtest, Walk-forward, Tarama:** hazır stratejiler ve script stratejileriyle.
- **Asistan:** sohbet geçmişi, araç çağrılarının canlı gösterimi, Markdown yanıtlar, scriptleri tek tıkla editörde açma, sembol ve editördeki scripti bağlam olarak ekleme.
- **Ayarlar:** yapay zeka sağlayıcısı (OpenRouter ya da fal.ai) ve anahtarı, asistan modeli, karar modeli ve hız testi, emir izni, görünüm.

<kbd>⌘/Ctrl</kbd>+<kbd>K</kbd> komut paletini açar (sembol, sayfa, script ve eylem araması). <kbd>B</kbd> emir, <kbd>A</kbd> asistan, <kbd>H</kbd> bakiyeleri gizle; editörde <kbd>Ctrl</kbd>+<kbd>↵</kbd> çalıştırır, <kbd>Ctrl</kbd>+<kbd>S</kbd> kaydeder.

```sh
marketalyzer-web                 # http://127.0.0.1:8000/?token=... adresini yazdırır
marketalyzer-web --demo          # internetsiz deneme: sentetik fiyatlar, ayrı "demo" hesabı
marketalyzer-web --new-token     # erişim anahtarını yeniler (eski adres ve oturumlar geçersiz olur)
scripts/tunnel.sh --hostname bist.alanadiniz.com   # sabit adresle Cloudflare tüneli (aşağıya bakın)
scripts/tunnel.sh --quick --demo # her seferinde değişen geçici adres; ek argümanlar marketalyzer-web'e geçer
```

Uygulama bir erişim anahtarıyla açılır. Anahtar ilk açılışta üretilir ve `~/.local/share/marketalyzer/web/token` dosyasında (izin 600) saklanır; sonraki açılışlarda aynısı kullanılır, böylece adres, tarayıcı oturumu ve telefona kurulan uygulama yeniden başlatmalarda bozulmaz. Adresteki `?token=...` bir kez kullanıldığında ya da giriş sayfasına anahtar yazıldığında tarayıcı onu 30 gün hatırlar; **Çıkış** bağlantısı çerezi siler. Anahtar sızarsa `marketalyzer-web --new-token` ile yenileyin. `MARKETALYZER_TOKEN` ortam değişkeni verilirse dosyanın yerine o kullanılır.

**Alan adı olmadan sabit adres: Netlify.** Arayüz `https://marketalyzer.netlify.app` adresinde Netlify'da durur, sunucu bu bilgisayarda tünelle çalışmaya devam eder. Her başlatmada betik hızlı tünelin yeni adresini Netlify'daki `backend.json` dosyasına yazar (yaklaşık 250 KB'lık bir yükleme); arayüz sunucuyu oradan bulur ve istekleri erişim anahtarıyla doğrudan tünele gönderir. Adres hiç değişmez; anahtar tarayıcıda bu adrese bağlı saklanır, telefona kurulan uygulama da aynı adreste kalır. Sunucu yalnızca bu siteden gelen tarayıcı isteklerine izin verir (CORS).

1. [app.netlify.com](https://app.netlify.com) hesabı açın. Netlify CLI ile giriş yaptıysanız (`netlify login`) başka bir şey gerekmez; yoksa **User settings → Applications → Personal access tokens** bölümünden bir anahtar oluşturun.
2. Bir kez: `marketalyzer-netlify setup` (CLI oturumu ya da `NETLIFY_AUTH_TOKEN` kullanılır; yoksa `--token <anahtar>`) — `marketalyzer` adlı siteyi oluşturur (ad başka hesapta kullanılıyorsa `--name` ile başka bir ad verin). Anahtar ve site bilgisi `~/.local/share/marketalyzer/netlify.json` dosyasında (izin 600) saklanır.
3. Her seferinde: `scripts/tunnel.sh` ya da `scripts\tunnel.ps1`. Bağlantı kurulunca `https://marketalyzer.netlify.app/?token=...` yazdırılır; ilk açılıştan sonra `?token=` olmadan da açılır.

Netlify yalnızca statik arayüzü barındırır: backtest, yapay zeka ve sanal hesap bilgisayarınızda çalışır. Bilgisayar ya da tünel kapalıyken site açılır ama "Sunucuya ulaşılamıyor" uyarısı verir. Netlify'ı bırakmak için `marketalyzer-netlify forget`.

**Kendi alan adınızla sabit tünel adresi.** Hızlı tünel (`--quick`, alan adı yokken varsayılan) her başlatmada yeni bir `https://<rastgele>.trycloudflare.com` adresi verir. Her zaman aynı adres için ücretsiz bir Cloudflare hesabı ve Cloudflare'e bağlı bir alan adı gerekir (alan adının ad sunucuları Cloudflare olmalı; Cloudflare Registrar'dan da alınabilir):

1. Bir kez giriş yapın: `cloudflared tunnel login` (tarayıcıda alan adınızı seçin).
2. İlk çalıştırmada adresi verin: `scripts/tunnel.sh --hostname bist.alanadiniz.com` (Windows: `scripts\tunnel.ps1 -Hostname bist.alanadiniz.com`). Betik `marketalyzer` adlı tüneli ve DNS kaydını oluşturur, adresi `~/.local/share/marketalyzer/tunnel.*` dosyasında hatırlar.
3. Sonraki çalıştırmalarda argüman gerekmez: `scripts/tunnel.sh` ya da `scripts\tunnel.ps1`. Kayıtlı adresi unutturmak için `--forget` / `-Forget`, farklı tünel adı için `--name` / `-TunnelName`.

Tüneli Cloudflare panelinden (Zero Trust → Networks → Tunnels, genel ad → `http://localhost:8000`) oluşturduysanız panelin verdiği anahtarı `CLOUDFLARE_TUNNEL_TOKEN` ortam değişkenine koyun; betik `cloudflared tunnel run --token` ile bağlanır (`--hostname` verilirse tam adresi yazdırır).

**Uygulama olarak kurma (PWA):** tünel adresi HTTPS olduğu için uygulama telefona ve masaüstüne kurulabilir.
- iPhone: Safari'de Paylaş → Ana Ekrana Ekle. Ana ekrandan ilk açılışta erişim anahtarını bir kez girin; ana ekran uygulamaları Safari'nin çerezlerini paylaşmaz.
- Android: Chrome menüsü → Uygulamayı yükle.
- Masaüstü (Chrome/Edge): adres çubuğundaki yükle simgesi.

**Windows:** [uv](https://docs.astral.sh/uv/) ve cloudflared'ı kurun (`winget install astral-sh.uv` ve `winget install --id Cloudflare.cloudflared`), sonra PowerShell'de:

```powershell
uv sync
.venv\Scripts\Activate.ps1
$env:OPENROUTER_API_KEY = "sk-or-..."      # isteğe bağlı; Ayarlar'dan da girilebilir
marketalyzer-netlify setup               # alan adı yoksa, bir kez (netlify login oturumu ya da --token <anahtar>)
powershell -ExecutionPolicy Bypass -File scripts\tunnel.ps1          # her seferinde
```

Kendi alan adınız varsa Netlify yerine: `cloudflared tunnel login` (bir kez), sonra `scripts\tunnel.ps1 -Hostname bist.alanadiniz.com`.

Betikler için [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/) kurulu olmalıdır. Bağlantı kurulunca anahtarla birlikte tam adres yazdırılır. Adresi bilen herkes uygulamaya erişebileceği için paylaşmayın. Tünel, betik çalıştığı sürece açık kalır.

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
6. **AI Strateji ve kör test:** sinyal araştırması, yapay zekanın araştırmadan yazdığı Pine Script, hızlı karar modeliyle bar bar kör backtest, sonuçlarından öğrenen karar günlüğü, pencere pencere script revizyonu (yapay zekalı walk-forward) ve sanal hesap için canlı karar. ✅ Sonraki adımlar: karar modelinin pozisyon büyüklüğü önermesi, gün içi barlarda kör test.
