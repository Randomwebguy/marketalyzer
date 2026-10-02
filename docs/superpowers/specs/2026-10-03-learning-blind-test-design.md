# Öğrenen kör test: tasarım

Tarih: 2026-10-03 · Durum: onaylandı

## Sorun

Son kör test (ENKAI, TUPRS, THYAO, BIMAS, ASELS; test 2025-10-02 → 2026-10-02,
1 yıl eğitim, karar modeli `google/gemini-3.8-flash`) 29 karar verdi, 1 işlem yaptı.
Karar önbelleğinden birebir yeniden oynatıldığında:

| | Getiri | İşlem | İsabet | En büyük düşüş |
|---|---|---|---|---|
| Yapay zekâ | +%2,27 | 1 | %100 | -%1,52 |
| Sadece sinyaller | +%4,43 | 28 | %53,6 | -%3,35 |
| Al-tut | +%43,9 | – | – | -%13,0 |
| XU100 | +%10,52 | – | – | – |

1. Model 28 girişin 27'sini reddetti; reddedilenlerin ortalaması +%0,58, 14/27'si
   kârlıydı. Filtre seçici değil, sadece katı.
2. Script bir dönüş (düşüşte alım) stratejisi; karar talimatı "trend ve momentum
   desteklemiyorsa reddet" diyor. Gerekçelerin neredeyse hepsi stratejinin giriş
   koşulunun kendisi ("negatif momentum, Supertrend aşağı").
3. Model stratejinin ne yaptığını, eğitimdeki başarısını ve kendi kararlarının
   sonuçlarını görmüyor.
4. Stratejinin tüm sinyalleri bile al-tutun çok gerisinde: getiri ve işlem sayısını
   kalıcı artırmak için script de gelişmeli.

## Hedef

Turların iyileştirmeye çalıştığı başarı: toplam getiride XU100'ü ve sadece-sinyal
sonucunu geçmek; yan koşullar hisse başına pencerede en az 1 işlem ve al-tuttan
küçük en büyük düşüş. İyileşme yalnızca ileriye dönük (sonraki pencerelerde)
ölçülür; aynı dönem üzerinde tekrar tekrar "öğrenmek" geleceği görmek olur.

## Tasarım

### 1. Karar katmanı

- **Strateji özeti.** Kör test başında karar modeline scriptin kaynağı verilip 2
  cümlelik özet ve tür (dönüş / trend / kırılım / karma) istenir; script kaynağının
  özetiyle önbelleğe alınır. Her görünüme `strateji_ozeti` eklenir.
- **Temel oran.** Script, her hissenin eğitim barlarında yapay zekâsız işletilir;
  toplam işlem, isabet, ortalama getiri ve ortalama süre görünüme
  `strateji_gecmisi` olarak eklenir.
- **Veto talimatı.** Sinyal varsayılan olarak uygulanır; stratejinin mantığının
  gerektirdiği koşullar (dönüş stratejisinde düşüş, aşırı satım) ret nedeni değildir;
  yalnızca belirgin ek risk varsa reddedilir. Güven, işlemin kârlı olma olasılığıdır.

### 2. Karar günlüğü (tek koşu içinde nedensel öğrenme)

- Hisseler ortak bir tarih takviminde birlikte ilerler; aynı tarihteki kararlar
  paralel sorulur. Öğrenme kapalıyken sonuçlar bugünkü sürümle birebir aynıdır.
- Her kararın sonucu yalnızca çözüldüğü bardan sonra günlüğe girer:
  - AL: işlem kapanınca gerçek getiri;
  - BEKLE (reddedilen giriş): aynı sinyalin yapay zekâsız işleminin sonucu, o işlem
    kapanınca (yoksa 10 bar sonraki getiri);
  - SAT / TUT: 10 bar sonra fiyatın ne yaptığı.
- Görünüme `karar_gunlugu`: özet (alınan girişlerin isabeti ve ortalaması,
  reddedilenlerin kaçının kâr ettireceği), son 6 sonuç ("Hisse A", tarih yok) ve ders
  notları.
- Ders notu: her 6 yeni sonuçta asistan modeli günlüğü okuyup en fazla 5 maddelik
  notu yeniden yazar. Notlar ve günlük hisse kodu / tarih sızıntısı için taranır.

### 3. Yürüyen pencere turları (script gelişimi)

- Tur sayısı 1–4; test dönemi eşit bar sayılı pencerelere bölünür.
- Pencere sonunda açık pozisyonlar bir sonraki açılışta "tur sonu" ile kapanır; pencere
  raporu (yapay zekâ, sadece-sinyal, al-tut, XU100, işlem sayısı, piyasada kalma,
  günlük özeti, ders notları) çıkarılır.
- Script yazarı mevcut scripti, pencere raporunu ve pencere sonuna kadar güncellenmiş
  sinyal araştırmasını görerek scripti revize eder; yeni script yalnızca pencere
  sonuna kadarki veriyle doğrulanır, başarısızsa eski script sürer. Yeni script
  yalnızca sonraki pencerede kullanılır ve `<ad>_t2`, `_t3`… olarak kaydedilir.
- Karşılaştırma: pencere scriptiyle sadece-sinyal ve ilk scriptle tüm dönem
  sadece-sinyal ayrı ayrı raporlanır.

### 4. Kayıt, canlı karar, arayüz

- Her koşu `ai/lab/runs/<id>.json` olarak saklanır (kararlar, günlük, notlar,
  scriptler, tur tablosu); son koşular API'den okunur.
- Canlı karar, aynı script için son öğrenme koşusunun strateji özetini ve ders
  notlarını kullanır (canlı karar her zaman testten sonradır).
- Kör test formunda "Öğrenme: kapalı / karar günlüğü / günlük + turlar" ve tur sayısı;
  tahmin ek çağrıları gösterir; akışta tur, ders notu ve revizyon olayları; sonuçta
  tur tablosu ve notlar.

## Körlük güvenceleri

- Günlükteki hiçbir sonuç, çözüldüğü bardan önceki bir karara gösterilmez.
- Ders notları yalnızca o ana kadar çözülmüş sonuçlardan yazılır.
- Revizyon yalnızca pencere sonuna kadarki veriyi görür ve yalnızca sonraki
  pencerede kullanılır.
- Görünüm, günlük ve notlar hisse kodu ve tarih için taranır; denetim kartı yeni
  sayıları da gösterir.

## Test

- Günlük nedenselliği (çözülmemiş sonuç görünmez), öğrenme kapalıyken eski sonuçla
  birebir eşitlik, pencere bölme, revizyon başarısızsa eski scriptin sürmesi, sızıntı
  taraması, kayıt.
- Gerçek doğrulama: yukarıdaki test fal.ai üzerinden yeniden koşulup tabloyla
  karşılaştırılır.

## Aşamalar

1. Karar katmanı + karar günlüğü + kayıt.
2. Yürüyen pencere turları + arayüz.
