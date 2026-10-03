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

Trendi yeniden girişlerle ya da sıkı stoplarla parçalamak işlem sayısını artırır ama maliyet ve testere zararıyla getiriyi düşürür; ortalama uzunlukları gibi parametrelerde bir yılın en iyisi diğer yılın en kötülerinden olabilir. Bu yüzden iki dönemde de tutarlı kalan hassas Supertrend seçildi. Ölçümler yükselen bir piyasaya aittir; düşen piyasada trend takibi daha az işlem ve farklı sonuç verir.

**Öğrenen kör test.** Kör test formundaki **Öğrenme** seçeneği:
- **Karar günlüğü:** hisseler ortak bir tarih takviminde birlikte ilerler; her kararın sonucu yalnızca gerçekleştiği bardan sonraki kararlara gösterilir. AL kararının sonucu açtığı işlemdir; reddedilen girişin sonucu aynı sinyalin yapay zekasız işlemidir (kaçırılan ya da kaçınılan getiri); SAT/TUT kararları sonraki 10 barla ölçülür. Görünüme bir özet (alınan girişlerin kazançlı oranı, reddedilenlerin kaçının kâr ettireceği), son 6 sonuç ve ders notları eklenir. Her 6 yeni sonuçta asistan modeli günlüğü okuyup en fazla 5 maddelik ders notunu yeniden yazar. Hisseler harfle anılır, tarih yoktur; hisse adı ya da tarih içeren notlar atılır. Test bitişini ileri almak önceki hiçbir kararın görünümünü değiştirmez (testle doğrulanır).
- **Günlük + script turları:** test dönemi 2–4 eşit pencereye bölünür. Her pencere sonunda açık pozisyonlar kapanır ("tur sonu"), sermaye sonraki pencereye taşınır ve script yazarı scripti pencerenin anonim raporu (yapay zeka, sinyaller, al-tut, XU100, hisse bazında sonuçlar, günlük özeti, ders notları) ve pencere sonuna kadar güncellenmiş sinyal araştırmasıyla geliştirir. Hedef: getiride XU100'ü ve yapay zekasız sonucu geçmek, her hissede pencere başına en az 1 işlem, al-tuttan küçük düşüş. Yeni script `<script>_t2`, `_t3`… olarak kaydedilir ve yalnızca sonraki pencerede kullanılır; doğrulanamazsa eski script sürer. Sonuçta tur tablosu ve ilk scriptin revizyonsuz tüm dönem sonucu da gösterilir.

Her yapay zekalı kör test `~/.local/share/marketalyzer/ai/lab/runs/` altına kaydedilir (`/api/lab/runs`). Canlı karar, aynı scriptle öğrenen en son kör testin ders notlarını kullanır.

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
