# marketalyzer

Borsa İstanbul (BIST) üzerinde yapay zeka destekli strateji denemeleri için bir çalışma alanı. Stratejiler önce geçmiş veriyle **backtest** edilir, sonra **gerçek para kullanılmadan** sanal bir portföyle canlı piyasada (paper trading) izlenir.

> Bu proje deneme ve öğrenme amaçlıdır. Gerçek emir göndermez ve yatırım tavsiyesi değildir.

## Yapı

```
providers/bist/   openbb-bist: OpenBB ODP V5 için BIST veri sağlayıcısı
```

Veri katmanı olarak [OpenBB Open Data Platform](https://github.com/openbq-org/openbb) kullanılır. `openbb-bist` sağlayıcısı BIST hisselerini, endekslerini ve TRY kurlarını OpenBB'nin standart komutlarına bağlar:

```python
from openbb import obb

prices = obb.equity.price.historical("THYAO", start_date="2024-01-01", provider="bist")
xu100 = obb.index.price.historical("XU100", provider="bist")
quote = obb.equity.price.quote("THYAO,GARAN", provider="bist")
```

Kurulum, semboller, periyotlar ve veri kısıtları için [providers/bist/README.md](providers/bist/README.md) dosyasına bakın.

Türkiye makro verisi OpenBB'nin kendi sağlayıcılarından alınabilir: IMF (`country="TUR"`), FRED (`country="turkey"`, API anahtarı gerekir) ve JODI (`country="turkiye"`). OpenBB V5'in OECD komutları Türkiye için şu an hata veriyor.

## Yol haritası

1. **Veri katmanı** (bu sürüm): `openbb-bist` sağlayıcısı.
2. **Backtest motoru:** BIST'e özgü maliyetler (komisyon, BSMV), fiyat adımları, seans saatleri ve T+2 takas ile bedelsiz düzeltmelerinin doğrulanması.
3. **Paper trading:** sanal bir aracı kurum ve emir defteri simülasyonu. Yahoo verisi yaklaşık 15 dakika gecikmeli olduğu için gerçek zamanlıya yakın test için lisanslı bir veri kaynağı gerekir.
4. **Yapay zeka katmanı:** OpenBB'nin MCP sunucusu (`openbb-mcp`) üzerinden veriye erişen ve stratejileri yalnızca backtest ve paper trading ortamında öneren veya değerlendiren bir ajan.
