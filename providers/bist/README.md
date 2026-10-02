# openbb-bist

Borsa İstanbul (BIST) için [OpenBB Open Data Platform](https://github.com/openbq-org/openbb) V5 sağlayıcısı. Bu sağlayıcı BIST hisselerini, endekslerini ve TRY kurlarını OpenBB'nin standart komutlarına bağlar. Böylece aynı veri Python'dan, REST API'den ve MCP üzerinden yapay zeka ajanlarından kullanılabilir.

## Kurulum

```sh
pip install openbb            # OpenBB ODP V5
pip install -e providers/bist # bu paket (depo kökünden)
```

Kurulumdan sonra ilk `from openbb import obb` çağrısı OpenBB'nin Python arayüzünü yeniden üretir ve `provider="bist"` seçeneği kullanılabilir hale gelir.

## Komutlar

| OpenBB komutu | Model | Örnek |
|---|---|---|
| `obb.equity.price.historical` | EquityHistorical | `obb.equity.price.historical("THYAO", provider="bist")` |
| `obb.equity.price.quote` | EquityQuote | `obb.equity.price.quote("THYAO,GARAN", provider="bist")` |
| `obb.index.price.historical` | IndexHistorical | `obb.index.price.historical("XU100", provider="bist")` |
| `obb.index.available` | AvailableIndices | `obb.index.available(provider="bist")` |
| `obb.currency.price.historical` | CurrencyHistorical | `obb.currency.price.historical("USDTRY", provider="bist")` |

```python
from openbb import obb

# Günlük, temettü düzeltmeli fiyatlar ve temettü/bölünme kolonları
df = obb.equity.price.historical(
    "THYAO,GARAN,ASELS",
    start_date="2024-01-01",
    adjustment="splits_and_dividends",
    include_actions=True,
    provider="bist",
).to_dataframe()

# Son 7 günün 5 dakikalık barları (İstanbul saatiyle)
bars = obb.equity.price.historical("THYAO", interval="5m", provider="bist")

# BIST 100 ve dolar/TL
xu100 = obb.index.price.historical("BIST100", provider="bist")
usdtry = obb.currency.price.historical("USDTRY", provider="bist")
```

### Semboller

- Hisseler BIST kodu ile yazılır: `THYAO`, `thyao`, `THYAO.E` ve `THYAO.IS` aynı hisseyi verir.
- Endeksler için `XU100`, `XU030` ve `XBANK` kullanılabilir. `BIST100`, `BIST50` ve `BIST30` takma adları da geçerlidir.
- Kurlar altı harfli çiftlerle yazılır: `USDTRY`, `EUR/TRY`.
- Birden fazla sembol virgülle ayrılır. Bu durumda sonuçlara bir `symbol` kolonu eklenir. Bulunamayan semboller hata vermez, uyarı olarak raporlanır.

### Periyotlar

`1m`, `2m`, `5m`, `15m`, `30m`, `1h`, `1d` (varsayılan), `1W`, `1M`.

| Periyot | Geriye dönük sınır | Başlangıç tarihi verilmezse |
|---|---|---|
| `1m` | 30 gün (6 günlük parçalarla çekilir) | son 7 gün |
| `2m`–`30m` | 60 gün | son 7 gün |
| `1h` | 730 gün | son 7 gün |
| `1d`, `1W`, `1M` | sınırsız | son 1 yıl |

Sınırın ötesindeki bir başlangıç tarihi otomatik olarak sınıra çekilir ve bir uyarı üretilir. Gün içi barlar İstanbul saat dilimindedir (`+03:00`). Günlük ve daha uzun periyotlarda tarih, işlem günüdür.

## Veri notları (backtest ve canlı test için önemli)

- **Kaynak Yahoo Finance chart API'sidir.** Resmi bir BIST veri lisansı değildir. Uç nokta haber verilmeden değişebilir. Yahoo, tarayıcıya benzemeyen isteklere ilk istekte bile erişim sınırı (HTTP 429) uyguladığı için istekler `curl_cffi` ile Chrome gibi gönderilir; aynı anda en fazla 4 istek yapılır, yanıtlar 60 saniye önbellekte tutulur ve 429 gelirse diğer sunucuda beklemeli olarak yeniden denenir. Sınır sürerse 30 saniye istek yapılmaz.
- **Veriler yaklaşık 15 dakika gecikmelidir.** Bu veriyle yapılan "canlı test", gecikmeli veriyle yapılan bir kağıt üzerinde işlemdir (paper trading). Gerçek zamanlı veri için lisanslı bir veri sağlayıcısı veya aracı kurum API'si gerekir.
- **Düzeltmeler:** `splits_only` (varsayılan) bölünme ve bedelsiz sermaye artırımlarına göre düzeltilmiş fiyatları verir. `splits_and_dividends` ayrıca nakit temettüleri de geriye doğru düzeltir. Yahoo'nun BIST'teki bedelsiz düzeltmeleri zaman zaman hatalı olabilir. Backtest öncesinde büyük fiyat sıçramalarını kontrol edin.
- Seviye 2 (derinlik) verisi, VİOP ve KAP bildirimleri kapsam dışıdır.

## Veri kaynağını değiştirmek

Tüm ağ çağrıları `openbb_bist/utils/yahoo.py` içindeki `get_chart` fonksiyonundan geçer. Fetcher'lar standart satırlar (`date`, `open`, `high`, `low`, `close`, `volume`) bekler. Başka bir kaynak (İş Yatırım, bir aracı kurum API'si, TCMB EVDS) eklemek için aynı satır biçimini döndüren yeni bir modül yazıp fetcher'larda onu çağırmak yeterlidir.

## Geliştirme

```sh
cd providers/bist
pip install -e . pytest ruff
pytest
ruff check . && ruff format --check .
python scripts/smoke_test.py   # canlı veriyle hızlı kontrol (internet gerekir)
```

Birim testleri ağa çıkmaz. Yahoo yanıtları chart API formatındaki sentetik verilerle taklit edilir.
