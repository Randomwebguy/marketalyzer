# Kripto araştırma turu: planlama ve uygulama

Tarih: 2026-10-04. Kullanıcı, araştırma sonuçlarına göre plan yapılıp hiçbir şey sorulmadan uygulanmasını istedi. Araştırma raporunun özeti README'de, kaynaklarıyla birlikte.

## Araştırmanın ana bulguları

1. **Hayatta kalma yanlılığı.** Bugünün 15 büyük coini geriye dönük seçildi. Eşit ağırlıklı ve kazananı kovalayan kurallarda bu yanlılık en büyüktür (Ammann ve diğ.). LUNA, FTT gibi çöken coinler o dönem uygun olurdu, ama listede yoklar.
2. **Oynaklığa göre boyut.** Düşüşü belirgin azaltır, getiriye etkisi zayıf ya da orta düzeydedir.
3. **Çok ufuklu Donchian topluluğu ve oynaklık hedefi** (Zarattini, Pagani & Barbon 2025): net Sharpe 1,5'in üstünde, düşüşler küçük.
4. **90 günlük çeyreklik sıralama** literatürde zayıf. Kesitsel momentum 1-4 haftalık ufukta görülüyor.
5. **Zamanlama şansı.** Çeyreklik ve yoğunlaşmış bir kuralda yeniden dengeleme tarihi sonucu belirgin oynatır.
6. **İstatistik.** Deflated Sharpe ve CSCV ile aşırı uyum olasılığı (PBO) ölçülmeden hiçbir şey benimsenmez.
7. **Fonlama.** Kalabalıklaşmayı gösterir, risk azaltıcı olarak kullanılabilir; kanıtı zayıf.
8. **Daha yavaş ya da topluluk BTC rejim filtresi.**

**Uygulanmayacaklar:** korku ve açgözlülük endeksi, LSTM/Transformer, carry getirisi, haftalık geri dönüş, yarılanma döngüleri, BTC hakimiyeti, haftanın günü etkileri.

**Kaldıraç:** en fazla 1,0x. Kendi testimiz de 1,5x'te bile Sharpe'ın düştüğünü gösterdi.

## Plan

1. **Veri** (`marketalyzer/crypto/binance.py`): Binance arşivinden (data.binance.vision) bütün USDT çiftlerinin günlük mumları, kaldırılmış coinler dahil.
   - Yerel önbellek kullanılır; anahtar gerekmez.
   - Bir sembolün verisinde 7 günden uzun boşluk varsa ayrı coin sayılır. Örnek: eski LUNA ile LUNA 2.0.
2. **O tarihteki evren** (`marketalyzer/crypto/universe.py`): her ay başında evren yeniden kurulur.
   - Uygunluk: en az 365 gündür işlem gören ve son 30 günün ortanca işlem hacmi en az 2 M $ olan coinler.
   - Hariç tutulanlar: sabit coinler, sarılı coinler ve kaldıraçlı tokenler.
   - Seçim: hacme göre ilk 20.
3. **Yeniden ölçüm:** mevcut kural (en güçlü 5 ve en güçlü 3) bu evrende yeniden ölçülür. Böylece yanlılığın etkisi görülür.
4. **Adaylar.** Parametreler literatürden alınır ve ayarlanmaz.
   - A. Mevcut kural ve oynaklık boyutu.
   - B. Sıralamasız: evrendeki her coinde supertrend ve BTC filtresi, oynaklık hedefiyle.
   - C. Donchian topluluğu: 9 ufuk, 90 günlük oynaklık, portföy oynaklık hedefi %25, brüt pozisyon en fazla 1,0x.
   - D. C'nin %40 oynaklık hedefli sürümü.
   - E. C ile topluluk BTC rejimi (20/50/100/200 günlük).
   - F. Haftalık 4 haftalık momentum: en güçlü 5'e girer, 8. sıranın altına düşünce çıkar. BTC filtresi uygulanır.
   - G. En iyi adaya fonlama kalabalığı katmanı.
5. **Yargı**
   - Dönemler: eğitim 2020-2023, test 2024 → 2026-09.
   - Maliyet stresi: 2 kat maliyetle tekrar.
   - Mevcut kural için 13 haftalık başlangıç kaydırmasıyla zamanlama şansı ölçülür.
   - Deflated Sharpe hesaplanırken önceki turlardaki denemeler de sayılır.
   - Bütün adaylar için PBO (CSCV, 16 blok) hesaplanır.
   - **Benimseme şartı:** testte Sharpe mevcut kuraldan yüksek ya da düşüş belirgin biçimde küçük olmalı, DSR ≥ 0,95 olmalı ve PBO < 0,5 olmalı. Seçim yalnızca eğitim dönemine göre yapılır.
6. **Ürün.** Kazanan aday, Binance verisiyle yeni bir sanal hesapta 7/24 çalışır. Mevcut hesaplar karşılaştırma için devam eder. Hiçbir aday geçmezse bu da raporlanır.

## Sonuç

Ayrıntılar ve tablo README'de, "Araştırma turu" başlığı altında.

- **Hiçbir aday benimseme şartının tamamını geçmedi.**
  - Önceden belirlenen kuralla seçilen E25'in DSR'si 0,53, PBO'su 0,49.
  - E25 test düşüşünü yarıya indirdi, ama Sharpe'ı sanal hesabın kuralından düşük kaldı (0,61'e karşı 0,82).
  - Bu yüzden ana hesap değiştirilmedi.
- **Hayatta kalma yanlılığı:** dürüst evrende kuralın 2021-23 sonucu 949 bin $ değil, 333 bin $. "En güçlü 3"ün üstünlüğü yanlılıktan geliyordu.
- **Tutarlı iyileştirmeler:** üç dilimle zamanlama şansını azaltmak ve oynaklığa göre boyutlamak.
- **Plandan bilinçli sapma:** hiçbir aday geçmediği halde iki hesap ileriye dönük teste alındı. Sanal hesap gerçek para içermez ve tamamen test dışı veri üretir; bu yüzden kanıtlanmamış adayların denenebileceği doğru yer burasıdır.
  - "Gelişmiş" hesap tutarlı iki iyileştirmeyi birleştirir: üç dilim ve oynaklık %40.
  - "Donchian" hesabı önceden seçilen E25'tir.
  - Arayüzde de "kanıtlanmamış" diye anlatılırlar.
- **Benimsenmeyenler:** fonlama kalabalığı katmanı, eğitim Sharpe'ını düşürdüğü için benimsenmedi.
