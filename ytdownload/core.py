"""İndirme, microSD tespiti ve kopyalama mantığı (arayüzden bağımsız)."""

from __future__ import annotations

import os
import platform
import re
import shutil
import string
import sys
import threading
import json
from concurrent.futures import ThreadPoolExecutor
import dataclasses
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import yt_dlp
from yt_dlp.postprocessor.common import PostProcessor
from yt_dlp.utils import DownloadCancelled

AUDIO_EXTS = {".mp3", ".m4a", ".opus", ".ogg", ".flac", ".wav"}

Log = Callable[[str], None]
Progress = Callable[[float, str], None]  # (0..1, açıklama)


class Cancelled(DownloadCancelled):
    """yt-dlp `ignoreerrors` açıkken bile yutulmaz; iptal hemen etki eder."""

    msg = "İptal edildi"


# --------------------------------------------------------------------------- #
# Harici araçlar (ffmpeg / deno)
# --------------------------------------------------------------------------- #
def find_ffmpeg() -> str | None:
    """Sistemdeki ffmpeg'i, yoksa imageio-ffmpeg ile gelen gömülü ffmpeg'i döndürür."""
    system = shutil.which("ffmpeg")
    if system:
        return system
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def find_deno() -> str | None:
    """YouTube imza çözümü için gereken deno JS çalışma zamanını bulur."""
    # PyInstaller ile paketlenmiş uygulamada deno, uygulamanın içine gömülüdür.
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        exe = os.path.join(bundle, "deno.exe" if os.name == "nt" else "deno")
        if os.path.exists(exe):
            return exe
    try:
        import deno

        path = deno.find_deno_bin()
        if path and os.path.exists(path):
            return path
    except Exception:
        pass
    return shutil.which("deno")


# --------------------------------------------------------------------------- #
# microSD / çıkarılabilir sürücü tespiti
# --------------------------------------------------------------------------- #
@dataclass
class Drive:
    path: str
    label: str
    free_bytes: int

    def display(self) -> str:
        return f"{self.label}  ({human_size(self.free_bytes)} boş)"


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} TB"


def _free(path: str) -> int:
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return 0


def _windows_drives() -> list[Drive]:
    import ctypes

    kernel32 = ctypes.windll.kernel32
    drives = []
    bitmask = kernel32.GetLogicalDrives()
    for i, letter in enumerate(string.ascii_uppercase):
        if not bitmask & (1 << i):
            continue
        root = f"{letter}:\\"
        # 2 = DRIVE_REMOVABLE (SD kart / USB bellek)
        if kernel32.GetDriveTypeW(root) != 2:
            continue
        name_buf = ctypes.create_unicode_buffer(261)
        ok = kernel32.GetVolumeInformationW(root, name_buf, 261, None, None, None, None, 0)
        if not ok:  # takılı kart yok (boş kart okuyucu yuvası)
            continue
        name = name_buf.value or "Çıkarılabilir Disk"
        drives.append(Drive(root, f"{letter}: {name}", _free(root)))
    return drives


def _mac_drives() -> list[Drive]:
    drives = []
    volumes = Path("/Volumes")
    if not volumes.is_dir():
        return drives
    for vol in sorted(volumes.iterdir()):
        try:
            # Sistem diski "/" ile aynı cihazdadır; onu atla.
            if not vol.is_dir() or not os.path.ismount(vol):
                continue
            if os.stat(vol).st_dev == os.stat("/").st_dev:
                continue
        except OSError:
            continue
        drives.append(Drive(str(vol), vol.name, _free(str(vol))))
    return drives


def _linux_drives() -> list[Drive]:
    drives = []
    user = os.environ.get("USER", "")
    for base in (f"/media/{user}", "/media", f"/run/media/{user}"):
        p = Path(base)
        if not p.is_dir():
            continue
        for vol in sorted(p.iterdir()):
            if vol.is_dir() and os.path.ismount(vol):
                drives.append(Drive(str(vol), vol.name, _free(str(vol))))
    return drives


def list_removable_drives() -> list[Drive]:
    system = platform.system()
    try:
        if system == "Windows":
            return _windows_drives()
        if system == "Darwin":
            return _mac_drives()
        return _linux_drives()
    except Exception:
        return []


# --------------------------------------------------------------------------- #
# İndirme
# --------------------------------------------------------------------------- #
_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_name(name: str, fallback: str = "Album") -> str:
    """FAT32/exFAT (SD kart) ve Windows için güvenli klasör adı."""
    name = _BAD_CHARS.sub("_", name).strip().rstrip(".")
    return name[:120] or fallback


def fetch_playlist_title(url: str) -> str:
    opts = {"quiet": True, "no_warnings": True, "extract_flat": "in_playlist", "skip_download": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False) or {}
    return info.get("title") or info.get("id") or "Album"


# --------------------------------------------------------------------------- #
# Kütüphane taraması: aynı şarkının iki kez indirilmesini önler
# --------------------------------------------------------------------------- #
_YT_ID = re.compile(r"(?:[?&]v=|youtu\.be/|/shorts/)([\w-]{11})")
_BRACKETS = re.compile(r"[\(\[\{][^\)\]\}]*[\)\]\}]")
_NOISE = re.compile(r"\b(official|music|lyric|lyrics|video|audio|hd|hq|4k|visualizer|clip|resmi|klip)\b")
_NON_WORD = re.compile(r"[^\w]+")


def _norm(text: str) -> str:
    text = _BRACKETS.sub(" ", text.casefold())
    text = _NOISE.sub(" ", text)
    return " ".join(_NON_WORD.sub(" ", text).replace("_", " ").split())


def song_key(artist: str | None, title: str | None) -> str | None:
    """Sanatçı + şarkı adından karşılaştırma anahtarı. İkisi de yoksa None."""
    if not artist or not title:
        return None
    artist = re.sub(r"\s*-\s*topic$", "", artist.strip(), flags=re.I)
    # "Sanatçı A, Sanatçı B" / "A & B" → sadece ilk sanatçı
    artist = re.split(r",|&| feat\.? | ft\.? ", artist, maxsplit=1, flags=re.I)[0]
    a, t = _norm(artist), _norm(title)
    # Başlık "Sanatçı - Şarkı" şeklindeyse sanatçı kısmını at
    if t.startswith(a + " "):
        t = t[len(a) + 1:]
    return f"{a}|{t}" if a and t else None


def _read_tags(path: Path) -> tuple[str | None, str | None, str]:
    """(sanatçı, şarkı adı, yorum/link) döndürür."""
    try:
        import mutagen

        f = mutagen.File(path)
    except Exception:
        return None, None, ""
    if f is None or f.tags is None:
        return None, None, ""
    tags = f.tags

    def first(*keys):
        for k in keys:
            try:
                v = tags.get(k) if hasattr(tags, "get") else None
            except Exception:
                v = None
            if v:
                v = v[0] if isinstance(v, list) else getattr(v, "text", [v])[0]
                return str(v)
        return None

    if hasattr(tags, "getall"):  # MP3 (ID3)
        comment = " ".join(str(t) for fr in tags.getall("COMM") for t in fr.text)
        comment += " ".join(str(t) for fr in tags.getall("TXXX") for t in fr.text)
        return first("TPE1"), first("TIT2"), comment
    # M4A / OGG / FLAC
    comment = " ".join(str(x) for k in ("\xa9cmt", "comment", "purl", "COMMENT", "PURL") for x in (tags.get(k) or []))
    return first("\xa9ART", "artist", "ARTIST"), first("\xa9nam", "title", "TITLE"), comment


ORIGINAL_TITLE = "ORIGINAL_TITLE"  # ad kısaltılınca orijinal başlık bu etikette saklanır


def _read_original_title(path: Path) -> str | None:
    try:
        import mutagen

        f = mutagen.File(path)
        tags = f.tags if f else None
        if tags is None:
            return None
        if hasattr(tags, "getall"):  # MP3
            frames = tags.getall(f"TXXX:{ORIGINAL_TITLE}")
            return str(frames[0].text[0]) if frames and frames[0].text else None
        value = tags.get(f"----:com.apple.iTunes:{ORIGINAL_TITLE}")
        return bytes(value[0]).decode("utf-8", "ignore") if value else None
    except Exception:
        return None


@dataclass
class Library:
    ids: dict[str, list[str]] = dataclasses.field(default_factory=dict)  # YouTube video id → dosya yolları
    keys: dict[str, list[str]] = dataclasses.field(default_factory=dict)  # sanatçı|şarkı → dosya yolları

    lock: threading.Lock = dataclasses.field(default_factory=threading.Lock)

    def find(self, info: dict, ext: str | None = None) -> str | None:
        """Bu şarkı daha önce indirildiyse mevcut dosyanın yolunu döndürür.

        `ext` (".mp3" gibi) verilirse yalnızca o formattaki kopyalar sayılır; böylece
        m4a'dan mp3'e geçen kullanıcı şarkıları yeni formatta tekrar indirebilir.
        """
        key = song_key(_info_artist(info), info.get("track") or info.get("title"))
        candidates = self.ids.get(info.get("id") or "", []) + (self.keys.get(key, []) if key else [])
        for where in candidates:
            if ext is None or where.lower().endswith(ext) or where.endswith(PENDING):
                return where
        return None

    def add(self, info: dict, where: str):
        if info.get("id"):
            self.ids.setdefault(info["id"], []).append(where)
        key = song_key(_info_artist(info), info.get("track") or info.get("title"))
        if key:
            self.keys.setdefault(key, []).append(where)


PENDING = " (bu indirme)"


def _info_artist(info: dict) -> str | None:
    artists = info.get("artists")
    if artists:
        return artists[0]
    return info.get("artist") or info.get("creator") or info.get("uploader")


CACHE_NAME = ".library_cache.json"


def scan_library(folders: list[str | Path], cache_file: Path | None = None, log: Log = print) -> Library:
    """Klasörlerdeki müzikleri tarar. Etiketler `cache_file` içinde saklanır; sonraki
    taramalarda yalnızca yeni veya değişmiş dosyaların etiketleri okunur."""
    cache: dict = {}
    if cache_file and cache_file.exists():
        try:
            cache = json.loads(cache_file.read_text("utf-8"))
        except (OSError, ValueError):
            cache = {}
    new_cache: dict = {}
    lib = Library()
    count = read = 0
    for folder in folders:
        folder = Path(folder)
        if not folder.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(folder):
            # Gizli sistem klasörlerini atla (.Trashes, .Spotlight-V100, System Volume Information...)
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and d != "System Volume Information"]
            for name in filenames:
                if name.startswith(".") or Path(name).suffix.lower() not in AUDIO_EXTS:
                    continue
                path = Path(dirpath) / name
                where = str(path)
                try:
                    st = path.stat()
                except OSError:
                    continue
                stamp = [st.st_size, int(st.st_mtime)]
                entry = cache.get(where)
                if not entry or entry.get("stamp") != stamp:
                    artist, title, comment = _read_tags(path)
                    title = _read_original_title(path) or title  # kısaltılmış dosyalar için
                    m = _YT_ID.search(comment)
                    if not title:  # etiket yoksa dosya adından: "01 - Şarkı.mp3"
                        title = re.sub(r"^\d+\s*-\s*", "", path.stem)
                    entry = {"stamp": stamp, "id": m.group(1) if m else None, "key": song_key(artist, title)}
                    read += 1
                new_cache[where] = entry
                if entry["id"]:
                    lib.ids.setdefault(entry["id"], []).append(where)
                if entry["key"]:
                    lib.keys.setdefault(entry["key"], []).append(where)
                count += 1
    if cache_file:
        # Şu an takılı olmayan kartların kayıtlarını da koru
        scanned = [str(Path(f)) for f in folders]
        for k, v in cache.items():
            if k not in new_cache and not any(k.startswith(f + os.sep) for f in scanned):
                new_cache[k] = v
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(new_cache, ensure_ascii=False), "utf-8")
        except OSError:
            pass
    log(f"Kütüphane tarandı: {count} şarkı ({read} yeni dosya okundu).")
    return lib


# --------------------------------------------------------------------------- #
# İndirme
# --------------------------------------------------------------------------- #
def fetch_playlist(url: str) -> tuple[str, list[dict]]:
    """(albüm adı, parçalar) döndürür. Parçalar sadece id/url/başlık içerir (hızlı)."""
    opts = {"quiet": True, "no_warnings": True, "extract_flat": "in_playlist", "skip_download": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False) or {}
    if info.get("_type") != "playlist":  # tek video linki
        return info.get("title") or "Album", [info]
    entries = [e for e in (info.get("entries") or []) if e]
    return info.get("title") or info.get("id") or "Album", entries


class _SetTags(PostProcessor):
    """Parça numarası ve albüm adını etiketlere yazılmak üzere bilgiye ekler."""

    def __init__(self, track: int, album: str):
        super().__init__()
        self.track, self.album = track, album

    def run(self, info):
        info["track_number"] = self.track
        if not info.get("album"):  # YouTube Music zaten doğru albüm adını verir
            info["album"] = self.album
        return [], info


class _Logger:
    def __init__(self, log: Log):
        self.log = log

    def debug(self, msg):
        pass

    info = warning = debug

    def error(self, msg):
        self.log(f"Hata: {msg}")


@dataclass
class Result:
    album_dir: Path
    card_dir: Path | None
    downloaded: int = 0
    skipped: int = 0
    failed: int = 0
    copied: int = 0


def clean_mac_junk(folder: Path) -> int:
    """macOS'un FAT/exFAT kartlara bıraktığı `._*` ve `.DS_Store` dosyalarını siler.
    Android telefonlar `._Şarkı.m4a` dosyalarını şarkı sanıp "desteklenmeyen format" der."""
    removed = 0
    if not folder.is_dir():
        return 0
    for dirpath, _dirs, files in os.walk(folder):
        for name in files:
            if name.startswith("._") or name == ".DS_Store":
                try:
                    os.remove(os.path.join(dirpath, name))
                    removed += 1
                except OSError:
                    pass
    return removed


def card_album_dir(card_root: str, sub_folder: str, album_name: str) -> Path:
    dest = Path(card_root)
    if sub_folder.strip():
        dest = dest / safe_name(sub_folder.strip(), "Music")
    return dest / album_name


def download_album(
    url: str,
    out_root: Path,
    audio_format: str = "m4a",
    quality: str = "192",
    card_root: str | None = None,
    sub_folder: str = "Music",
    workers: int = 3,
    rename_opts=None,
    log: Log = print,
    progress: Progress = lambda f, s: None,
    is_cancelled: Callable[[], bool] = lambda: False,
) -> Result:
    """Albümü `out_root/<Albüm Adı>/` klasörüne indirir; `card_root` verilirse her şarkıyı
    iner inmez karta kopyalar.

    - `workers` parça aynı anda indirilir.
    - audio_format="m4a": YouTube'un AAC sesi dönüştürülmeden kopyalanır (kalite kaybı yok).
      audio_format="mp3": ses MP3'e dönüştürülür (`quality` kbps).
    - Bilgisayarda veya kartta zaten bulunan şarkılar atlanır.
    - `rename_opts` (rename.RenameOptions) verilirse şarkı adları kısaltılır.
    """
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg bulunamadı. 'pip install imageio-ffmpeg' komutunu çalıştırın.")

    log("Albüm bilgisi alınıyor...")
    title, entries = fetch_playlist(url)
    if not entries:
        raise RuntimeError("Bu linkte indirilecek şarkı bulunamadı.")
    album_dir = out_root / safe_name(title)
    album_dir.mkdir(parents=True, exist_ok=True)
    card_dir = card_album_dir(card_root, sub_folder, album_dir.name) if card_root else None
    log(f"Albüm: {title}  ({len(entries)} şarkı)")
    log(f"İndirme klasörü: {album_dir}")

    log("Daha önce indirilen şarkılar kontrol ediliyor...")
    library = scan_library([out_root, *([card_root] if card_root else [])], out_root / CACHE_NAME, log)

    result = Result(album_dir, card_dir)
    total = len(entries)
    fracs = [0.0] * total
    active: set[int] = set()
    state_lock = threading.Lock()
    deno_bin = find_deno()
    if not deno_bin:
        log("Uyarı: deno bulunamadı; bazı YouTube videoları indirilemeyebilir.")

    def report():
        with state_lock:
            done = sum(1 for f in fracs if f >= 1)
            overall = sum(fracs) / total
            n_active = len(active)
        progress(overall, f"{done}/{total} şarkı tamamlandı" + (f" · {n_active} iniyor" if n_active else ""))

    def set_frac(i, value):
        with state_lock:
            fracs[i] = max(fracs[i], value)
        report()

    # Karta yazma tek iş parçacığında: SD kartlar aynı anda birden çok yazmada yavaşlar
    copier = ThreadPoolExecutor(max_workers=1) if card_dir else None

    def copy_one(src: Path):
        if is_cancelled():
            return
        try:
            card_dir.mkdir(parents=True, exist_ok=True)
            target = card_dir / src.name
            if target.exists() and target.stat().st_size == src.stat().st_size:
                return
            size = src.stat().st_size
            free = _free(card_root)
            if free and size > free:
                raise RuntimeError(f"Kartta yer kalmadı ({human_size(free)} boş).")
            # copyfile: FAT32'de izin/tarih kopyalama hatalarını önler
            shutil.copyfile(src, target)
            with state_lock:
                result.copied += 1
            log(f"  → karta kopyalandı: {src.name}")
            # Aynı şarkının başka formattaki eski kopyası kartta kalmasın (ör. m4a → mp3 geçişi)
            for ext in AUDIO_EXTS - {src.suffix.lower()}:
                old = card_dir / (src.stem + ext)
                if old.exists():
                    old.unlink()
                    log(f"  ✗ eski format kaldırıldı: {old.name}")
        except Exception as e:  # noqa: BLE001
            log(f"Hata: karta kopyalanamadı: {src.name}: {e}")

    def one_track(i: int, entry: dict):
        if is_cancelled():
            return
        with state_lock:
            active.add(i)
        track_no = entry.get("playlist_index") or i + 1
        name = entry.get("title") or entry.get("id")
        skipped = []

        def hook(d):
            if is_cancelled():
                raise Cancelled()
            if d["status"] == "downloading":
                tot = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                if tot:
                    set_frac(i, d.get("downloaded_bytes", 0) / tot * 0.9)

        def skip_duplicates(info, *, incomplete):
            if incomplete:
                return None
            with library.lock:
                existing = library.find(info, "." + audio_format)
                if not existing:
                    # Aynı albümde iki kez geçse bile bir kez indir
                    library.add(info, album_dir.name + PENDING)
            if existing:
                skipped.append(existing)
                log(f"↷ Atlandı, zaten var: {info.get('track') or info.get('title')}  ({existing})")
                return "zaten indirilmiş"
            return None

        opts = {
            "format": "bestaudio[ext=m4a]/bestaudio/best" if audio_format == "m4a" else "bestaudio/best",
            "outtmpl": str(album_dir / f"{track_no:02d} - %(title)s.%(ext)s"),
            "windowsfilenames": True,  # SD kartta (FAT32/exFAT) geçersiz karakterleri temizler
            "ignoreerrors": True,  # tek parça hata verirse diğerleri devam etsin
            "noplaylist": True,
            "writethumbnail": True,
            "match_filter": skip_duplicates,
            "ffmpeg_location": ffmpeg,
            "progress_hooks": [hook],
            "postprocessors": [
                # m4a: kaynak AAC ise ffmpeg sadece kopyalar (yeniden kodlama yok)
                {"key": "FFmpegExtractAudio", "preferredcodec": audio_format, "preferredquality": quality},
                {"key": "FFmpegMetadata", "add_metadata": True},
                {"key": "EmbedThumbnail", "already_have_thumbnail": False},
            ],
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "logger": _Logger(log),
        }
        if deno_bin:
            opts["js_runtimes"] = {"deno": {"path": deno_bin}}
        video_url = entry.get("url") or entry.get("webpage_url") or entry.get("id")
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.add_post_processor(_SetTags(track_no, title), when="pre_process")
                info = ydl.extract_info(video_url, download=True)
            downloads = (info or {}).get("requested_downloads") or []
            path = downloads[0].get("filepath") if downloads else None
            ok = bool(path and os.path.exists(path))
            if ok and rename_opts:
                from .rename import shorten_downloaded

                try:
                    path = str(shorten_downloaded(Path(path), info, track_no, rename_opts))
                except Exception as e:  # noqa: BLE001 - kısaltılamazsa uzun adla devam et
                    log(f"Uyarı: ad kısaltılamadı ({Path(path).name}): {e}")
            with state_lock:
                if ok:
                    result.downloaded += 1
                elif skipped:
                    result.skipped += 1
                else:
                    result.failed += 1
            if ok:
                log(f"✓ {Path(path).stem}")
                if copier:
                    copier.submit(copy_one, Path(path))
            elif not skipped:
                log(f"✗ İndirilemedi: {name}")
        finally:
            with state_lock:
                active.discard(i)
            set_frac(i, 1.0)

    log(f"İndirme başlıyor ({max(1, workers)} şarkı aynı anda)...")
    report()
    try:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = [pool.submit(one_track, i, e) for i, e in enumerate(entries)]
            for f in futures:
                try:
                    f.result()
                except Cancelled:
                    pass
    finally:
        if copier:
            copier.shutdown(wait=True)
    if is_cancelled():
        raise Cancelled()

    # Önceden bilgisayara inmiş ama kartta olmayan şarkıları da tamamla
    if card_dir:
        card_prefix = str(Path(card_root)).rstrip(os.sep) + os.sep

        def on_card(p: Path) -> bool:
            if (card_dir / p.name).exists() and (card_dir / p.name).stat().st_size == p.stat().st_size:
                return True
            # Kartta başka adla (ör. kısaltılmış) duruyorsa YouTube kimliğinden tanı
            m = _YT_ID.search(_read_tags(p)[2])
            return bool(m) and any(x.startswith(card_prefix) and x.lower().endswith(p.suffix.lower())
                                   for x in library.ids.get(m.group(1), []))

        leftovers = [p for p in sorted(album_dir.iterdir())
                     if p.is_file() and p.suffix.lower() == "." + audio_format and not on_card(p)]
        for p in leftovers:
            if is_cancelled():
                raise Cancelled()
            copy_one(p)
        # Önceki albümlerde kalanlar dahil tüm müzik klasörünü temizle
        removed = clean_mac_junk(card_dir.parent if sub_folder.strip() else card_dir)
        if removed:
            log(f"Karttaki {removed} gizli Mac dosyası (._*) temizlendi.")

    summary = f"{result.downloaded} şarkı indirildi"
    if result.skipped:
        summary += f", {result.skipped} şarkı zaten vardı (atlandı)"
    if result.failed:
        summary += f", {result.failed} şarkı indirilemedi"
    if card_dir:
        summary += f", {result.copied} şarkı karta kopyalandı"
    log(summary + ".")
    progress(1.0, "Tamamlandı")
    return result
