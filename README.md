# YouTube Albüm → microSD

YouTube / YouTube Music'teki bir **albümün veya çalma listesinin tüm şarkılarını** tek tıkla
MP3 (veya M4A) olarak indirip **microSD karta otomatik aktaran** küçük bir masaüstü uygulaması.
**Windows** ve **macOS**'ta çalışır.

## Özellikler
- Albüm / playlist linkini yapıştır → **İndir ve Aktar**
- **M4A (orijinal kalite):** YouTube'un sesi dönüştürülmeden, birebir kopyalanır (kalite kaybı yok).
  MP3 seçilirse ses MP3'e dönüştürülür (eski cihazlar için).
- **Aynı şarkı iki kez inmez:** bilgisayardaki indirme klasörü ve microSD kart taranır;
  aynı şarkı başka bir albümde / başka bir videoda indirilmiş olsa bile atlanır
- Şarkılar `01 - Şarkı Adı.mp3` şeklinde sıralı adlandırılır
- Parça numarası, albüm adı, sanatçı ve kapak resmi dosyaya gömülür (araba teyplerinde / MP3 çalarlarda düzgün görünür)
- **Hızlı:** aynı anda 3 şarkı iner (1-6 arası ayarlanabilir) ve her şarkı iner inmez karta
  kopyalanır; albüm bittiğinde kopyalama da bitmiş olur
- **Şarkı adlarını kısaltma (tuşlu / küçük ekranlı telefonlar için):**
  `04 - Gripin- Durma Yağmur Durma (piyano cover)- İlayda Su Çakıroğlu.mp3` → `Durma Yağmur Durma.mp3`
  (başa sıra numarası istenirse **Numara** kutusu işaretlenir).
  Hem dosya adı hem dosyanın içindeki şarkı adı etiketi kısaltılır. İki yol var:
  - İndirirken: **Şarkı adları → Kısalt** kutusunu işaretleyin.
  - Mevcut şarkılar için: **Şarkı adlarını kısalt...** düğmesi. Önce önizleme gösterir; adı elle
    düzeltmek için satıra çift tıklayın, istemediğinizi **Seçilenleri atla** ile çıkarın, **Uygula**
    deyin. Beğenmezseniz **Geri Al**.
  - Deneme (hiçbir şeyi değiştirmez): `python -m ytdownload.rename "<klasör>"`
- microSD kart otomatik algılanır (Windows: çıkarılabilir sürücüler, Mac: `/Volumes`)
- Kopyalamadan önce kartta yeterli boş alan kontrol edilir
- Karttaki yol: `<Kart>/Music/<Albüm Adı>/` (klasör adı değiştirilebilir)
- Yarıda kalırsa tekrar çalıştırın: zaten inen/kopyalanan şarkılar atlanır
  (bir şarkıyı yeniden indirmek isterseniz dosyasını silmeniz yeterli)
- İlerleme çubuğu, günlük penceresi ve İptal düğmesi

## Kurulum ve çalıştırma

Gereken tek şey **Python 3.10+** ([python.org](https://www.python.org/downloads/)).
ffmpeg ve diğer her şey otomatik kurulur.

### Windows
1. Python'u kurarken **"Add python.exe to PATH"** kutusunu işaretleyin.
2. Bu klasördeki **`run_windows.bat`** dosyasına çift tıklayın.
   (İlk açılışta gerekli paketler kurulur, 1-2 dakika sürebilir.)

### macOS
1. Python'u python.org'dan kurun (Tkinter arayüzü onunla birlikte gelir).
2. Terminal'de bir kez: `chmod +x run_mac.command`
3. **`run_mac.command`** dosyasına çift tıklayın.
   (Güvenlik uyarısı çıkarsa: sağ tık → **Aç**.)

### Elle çalıştırma
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS: source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

## Kullanım
1. microSD kartı bilgisayara takın, uygulamada **Yenile**'ye basın ve kartı seçin
   (listede yoksa **Klasör...** ile kartı elle seçebilirsiniz).
2. YouTube Music'te albümü açın, adres çubuğundaki linki kopyalayın
   (ör. `https://music.youtube.com/playlist?list=OLAK5uy_...`) ve **Yapıştır**'a basın.
3. **İndir ve Aktar**'a basın.
4. Bitince kartı işletim sisteminden **Güvenli Çıkar** ile çıkarın.

Şarkılar ayrıca bilgisayarınızda `~/Music/YTDownload/<Albüm Adı>/` klasöründe saklanır.

## Tek dosya uygulama (.exe / .app) oluşturma
`ci/build.yml` GitHub Actions ile Windows ve macOS için hazır uygulama üretir.
Etkinleştirmek için dosyayı `.github/workflows/build.yml` konumuna taşıyın
(GitHub'da *Add file → Create new file* ile de ekleyebilirsiniz). Sonra
Actions sekmesi → *Build apps* → *Run workflow*, ya da `v1.0` gibi bir etiket push'layın.
Oluşan zip dosyaları Python kurulumu gerektirmez.

## Sorun giderme
- **Bazı şarkılar inmedi:** yt-dlp'yi güncelleyin: `pip install -U "yt-dlp[default]"`
  (`run_*.bat/.command` her açılışta bunu otomatik yapar). YouTube sık değişiklik yapar.
- **İndirmeler hata veriyor / YouTube engelliyor:** "Aynı anda" sayısını 1-2'ye düşürün.
- **Kopyalama yavaş:** kartın U3/V30/A1 sınıfı ve USB 3 kart okuyucu kullanın; hızı çoğunlukla kart belirler.
- **Telefonda "desteklenmeyen format" uyarısı:** Mac'in karta bıraktığı gizli `._` dosyalarından kaynaklanır;
  program her aktarımda bunları siler. Telefon M4A çalamıyorsa format olarak **mp3** seçip albümü
  tekrar çalıştırın: şarkılar MP3 olarak iner ve karttaki eski M4A kopyaları kaldırılır.
- **Kart görünmüyor:** kartın bilgisayarda açıldığından emin olun, **Yenile**'ye basın veya **Klasör...** ile seçin.

## Not
Yalnızca indirme hakkına sahip olduğunuz içerikleri indirin (ör. telifsiz/izinli müzik, kendi yüklemeleriniz).
YouTube Hizmet Şartları ve telif hakkı yasalarına uymak kullanıcının sorumluluğundadır.
