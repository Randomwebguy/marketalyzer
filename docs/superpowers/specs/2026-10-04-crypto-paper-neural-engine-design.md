# Kripto sanal hesap (7/24) ve NeuralEngine — tasarım

Tarih: 2026-10-04. Onay: kullanıcı "Öğrenen uzman seçimi" ve "Önce sanal hesap" seçeneklerini seçti.

## Amaç

1. Kripto için en sağlam bulunan yapay zekasız kuralı sanal hesapta 7/24 çalıştırmak.
2. Hatalarından ders çıkaran bir öğrenme motoru (NeuralEngine) kurmak. Motor önce geçmişte sınanacak, sonra sanal hesabın yanında gölge modda çalışacak.

## Aşama 1: Kripto sanal hesabı

**Kural** (`scripts/crypto/momentum_trend.py` ile sınandı):

- Her çeyrek başında 15 coin, son 90 günlük getiriye göre sıralanır ve en güçlü 5'i seçilir. Sıralama, çeyrek başından önceki kapanışlara göre yapılır.
- Seçilen her coinde supertrend (çarpan 2, ATR 10) yukarı döndüğü günün kapanışında, BTC 50 günlük ortalamasının üstündeyse alınır.
- Supertrend aşağı dönünce satılır.
- Seçimden düşen coinler satılır. Seçimde kalan coinlerin pozisyonları korunur. Sınama çerçevesi çeyrek sonunda kapatıyordu; bu bir test kalıbıydı, kuralın parçası değil.
- Her alım, o anki özsermayenin 1/5'i kadar yapılır; nakit yetmezse eldeki nakit kadar.

**Maliyet:** işlem başına %0,1 komisyon ve toplam %0,1 kayma (her tarafta %0,05).

**Sanal defter** (`marketalyzer/crypto/ledger.py`):

- Bir sqlite dosyasında USD nakit, kesirli miktarlı pozisyonlar, gerçekleşen işlemler, özsermaye kayıtları ve durum bilgisi tutulur.
- BIST sanal hesabı (tam lot, BIST fiyat adımları, seanslar) kullanılmaz ve değişmez.

**Zaman:**

- Kripto günlük barı UTC gününe aittir. Bir gün, ancak UTC takviminde bittikten sonra tamamlanmış sayılır.
- Çalıştırıcı (`marketalyzer-crypto step`) saatte bir çalışır. Tamamlanmış ve henüz işlenmemiş günleri sırayla işler; kararlar o anki fiyattan, kayma ve komisyonla gerçekleşir.
- Her çalışmada özsermaye o anki fiyatla kaydedilir. Kaçırılan günler sonradan işlenir.

**Veri:** Yahoo. Sembol dönüştürücü, içinde "-" geçen kodları (`BTC-USD`) olduğu gibi bırakır; BIST kodlarında "-" yoktur.

**Arayüz:** `GET /api/crypto` hesabın durumunu, pozisyonları, işlemleri, özsermaye eğrisini ve çeyreğin seçimini verir. Strateji sayfasında bir kart bunları gösterir.

**VPS:** `marketalyzer-crypto.timer` her saat çalışır. Başlangıç sermayesi 100.000 $.

## Aşama 2: NeuralEngine — öğrenen uzman seçimi

**Uzmanlar** (sınanmış, yapay zekasız kurallar; hepsi gölgede günlük işlem yapar):

- en güçlü 5 + supertrend + BTC filtresi (sanal hesabın kuralı)
- en güçlü 3 + aynı kural
- 15 coinde aynı kural
- BTC 200 günlük filtreli momentum rotasyonu
- SMA 20/100 + BTC filtresi
- nakit

**Durum:** BTC'nin 50 ve 200 günlük ortalamaya göre konumu, oynaklık dilimi, genişlik (50 günlük ortalamanın üstündeki coin oranı) ve momentum dağınıklığı. Tablo biçiminde, birkaç kova.

**Öğrenme:**

- Her gün her uzmanın gerçekleşen getirisi ödüldür. Motor, durum başına uzman ağırlıklarını çarpımsal ağırlıklarla (Hedge/EXP3 türü) günceller: zarar ettiren uzmanın ağırlığı düşer.
- Hiçbir uzman tamamen unutulmaz (taban ağırlık).
- Öğrenme hızı ve taban yalnızca 2021–2023'te seçilir.

**Karar:** sermaye, ağırlıklarla uzmanların pozisyonları arasında paylaştırılır.

**Doğrulama:**

- 2024–2026'da ileriye yürüyerek sabit kurala, al-tuta ve BTC'ye karşı sınanır.
- Sabit kuraldan iyi ya da aynı getiriyi daha az düşüşle verirse gölge modda sanal hesabın yanında çalışır ve günlük ne öğrendiğini raporlar.
- Sinir ağı katmanı (durumdan uzman ödülü tahmini) ancak tablo motorunu ileriye yürüyen testte geçerse eklenir.

## Sonuç (2026-10-04)

- Aşama 1 VPS'te çalışıyor: `marketalyzer-crypto.timer` her saat, başlangıç 100.000 $, Q4 seçimi LINK AVAX ETH LTC SOL.
- Aşama 2 doğrulamayı geçemedi (`scripts/crypto/neural_engine.py`, 96 ayar). 2024 → 2026-10 döneminde, 100 000 $ başlangıçla:
  - Seçilen ayar 190 200 $ yaptı; en büyük düşüş %-30, Sharpe 0,80.
  - Sabit kural 246 900 $ yaptı; en büyük düşüş %-41, Sharpe 0,95.
  - Uzmanların eşit karışımı 194 400 $ yaptı.
  - Hiçbir ayar sabit kuralı geçmedi.
- Motor gölge moda alınmadı. Sinir ağı katmanı tablo motorunun geçmesine bağlıydı; o da eklenmedi.

## Riskler

- Coin listesi bugünün büyüklerinden oluşuyor (hayatta kalma yanlılığı).
- Yahoo verisi gecikebilir ya da eksik olabilir. Çalıştırıcı eksik günü atlamaz, sonraki çalışmada işler.
