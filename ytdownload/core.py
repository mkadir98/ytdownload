"""İndirme, microSD tespiti ve kopyalama mantığı (arayüzden bağımsız)."""

from __future__ import annotations

import os
import platform
import re
import shutil
import string
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import yt_dlp
from yt_dlp.postprocessor.metadataparser import MetadataParserPP

AUDIO_EXTS = {".mp3", ".m4a", ".opus", ".ogg", ".flac", ".wav"}

Log = Callable[[str], None]
Progress = Callable[[float, str], None]  # (0..1, açıklama)


class Cancelled(Exception):
    pass


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


@dataclass
class Library:
    ids: dict[str, str]  # YouTube video id → dosya yolu
    keys: dict[str, str]  # sanatçı|şarkı → dosya yolu

    def find(self, info: dict) -> str | None:
        """Bu şarkı daha önce indirildiyse mevcut dosyanın yolunu döndürür."""
        vid = info.get("id")
        if vid and vid in self.ids:
            return self.ids[vid]
        key = song_key(_info_artist(info), info.get("track") or info.get("title"))
        return self.keys.get(key) if key else None

    def add(self, info: dict, where: str):
        if info.get("id"):
            self.ids[info["id"]] = where
        key = song_key(_info_artist(info), info.get("track") or info.get("title"))
        if key:
            self.keys[key] = where


def _info_artist(info: dict) -> str | None:
    artists = info.get("artists")
    if artists:
        return artists[0]
    return info.get("artist") or info.get("creator") or info.get("uploader")


def scan_library(folders: list[str | Path], log: Log = print) -> Library:
    lib = Library({}, {})
    count = 0
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
                artist, title, comment = _read_tags(path)
                where = str(path)
                m = _YT_ID.search(comment)
                if m:
                    lib.ids.setdefault(m.group(1), where)
                if not title:  # etiket yoksa dosya adından: "01 - Şarkı.mp3"
                    title = re.sub(r"^\d+\s*-\s*", "", path.stem)
                key = song_key(artist, title)
                if key:
                    lib.keys.setdefault(key, where)
                count += 1
    log(f"Kütüphane tarandı: {count} şarkı bulundu.")
    return lib


def download_album(
    url: str,
    out_root: Path,
    audio_format: str = "m4a",
    quality: str = "192",
    library_dirs: list[str | Path] | None = None,
    log: Log = print,
    progress: Progress = lambda f, s: None,
    is_cancelled: Callable[[], bool] = lambda: False,
) -> Path:
    """Albümü/çalma listesini `out_root/<Albüm Adı>/` klasörüne indirir ve klasörü döndürür.

    audio_format="m4a": YouTube'un AAC sesi dönüştürülmeden kopyalanır (kalite kaybı yok).
    audio_format="mp3": ses MP3'e dönüştürülür (`quality` kbps).
    Daha önce indirilmiş şarkılar (out_root ve library_dirs içinde) atlanır.
    """
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg bulunamadı. 'pip install imageio-ffmpeg' komutunu çalıştırın.")

    log("Albüm bilgisi alınıyor...")
    title = fetch_playlist_title(url)
    album_dir = out_root / safe_name(title)
    album_dir.mkdir(parents=True, exist_ok=True)
    log(f"Albüm: {title}")
    log(f"İndirme klasörü: {album_dir}")

    log("Daha önce indirilen şarkılar kontrol ediliyor...")
    library = scan_library([out_root, *(library_dirs or [])], log)
    stats = {"skipped": 0}

    def skip_duplicates(info, *, incomplete):
        if incomplete or info.get("_type") == "playlist":
            return None
        if is_cancelled():
            raise Cancelled()
        existing = library.find(info)
        name = info.get("track") or info.get("title") or info.get("id")
        if existing:
            stats["skipped"] += 1
            log(f"↷ Atlandı, zaten var: {name}  ({existing})")
            return "zaten indirilmiş"
        # Aynı albümde iki kez geçse bile bir kez indir
        library.add(info, f"{album_dir.name} (bu indirme)")
        return None

    state = {"index": 0, "total": 0}

    def hook(d):
        if is_cancelled():
            raise Cancelled()
        info = d.get("info_dict") or {}
        state["index"] = info.get("playlist_index") or state["index"] or 1
        state["total"] = info.get("n_entries") or info.get("playlist_count") or state["total"] or 1
        done_tracks = state["index"] - 1
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            frac = (d.get("downloaded_bytes", 0) / total) if total else 0
            progress(
                (done_tracks + frac * 0.9) / state["total"],
                f"[{state['index']}/{state['total']}] {info.get('title', '')}",
            )
        elif d["status"] == "finished":
            progress((done_tracks + 0.9) / state["total"], f"[{state['index']}/{state['total']}] dönüştürülüyor...")

    def pp_hook(d):
        if is_cancelled():
            raise Cancelled()
        if d["status"] == "finished" and d.get("postprocessor") == "MoveFiles":
            info = d.get("info_dict") or {}
            log(f"✓ {info.get('title', '')}")

    postprocessors = [
        {
            # Parça numarası ve albüm adını etiketlere yaz
            "key": "MetadataParser",
            "when": "pre_process",
            "actions": [
                (MetadataParserPP.interpretter, "playlist_index", "%(track_number)s"),
                (MetadataParserPP.interpretter, "playlist_title", "%(album)s"),
            ],
        },
        # m4a: kaynak AAC ise ffmpeg sadece kopyalar (yeniden kodlama yok)
        {"key": "FFmpegExtractAudio", "preferredcodec": audio_format, "preferredquality": quality},
        {"key": "FFmpegMetadata", "add_metadata": True},
        {"key": "EmbedThumbnail", "already_have_thumbnail": False},
    ]

    opts = {
        "format": "bestaudio[ext=m4a]/bestaudio/best" if audio_format == "m4a" else "bestaudio/best",
        "match_filter": skip_duplicates,
        "outtmpl": str(album_dir / "%(playlist_index)02d - %(title)s.%(ext)s"),
        "windowsfilenames": True,  # SD kartta (FAT32/exFAT) geçersiz karakterleri temizler
        "ignoreerrors": True,  # tek parça hata verirse albümün geri kalanı devam etsin
        "writethumbnail": True,
        "noplaylist": False,
        "ffmpeg_location": ffmpeg,
        "progress_hooks": [hook],
        "postprocessor_hooks": [pp_hook],
        "postprocessors": postprocessors,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
    }
    deno_bin = find_deno()
    if deno_bin:
        opts["js_runtimes"] = {"deno": {"path": deno_bin}}
    else:
        log("Uyarı: deno bulunamadı; bazı YouTube videoları indirilemeyebilir.")

    class _Logger:
        def debug(self, msg):
            pass

        def info(self, msg):
            pass

        def warning(self, msg):
            pass

        def error(self, msg):
            log(f"Hata: {msg}")

    opts["logger"] = _Logger()

    log("İndirme başlıyor...")
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    if is_cancelled():
        raise Cancelled()
    if stats["skipped"]:
        log(f"{stats['skipped']} şarkı zaten indirilmiş olduğu için atlandı.")
    progress(1.0, "İndirme tamamlandı")
    return album_dir


# --------------------------------------------------------------------------- #
# SD karta kopyalama
# --------------------------------------------------------------------------- #
def copy_to_card(
    album_dir: Path,
    card_root: str,
    sub_folder: str = "Music",
    log: Log = print,
    progress: Progress = lambda f, s: None,
    is_cancelled: Callable[[], bool] = lambda: False,
) -> Path | None:
    files = sorted(p for p in album_dir.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXTS)
    if not files:
        log("Karta kopyalanacak yeni şarkı yok (hepsi zaten mevcut).")
        progress(1.0, "Yeni şarkı yok")
        return None

    dest = Path(card_root)
    if sub_folder.strip():
        dest = dest / safe_name(sub_folder.strip(), "Music")
    dest = dest / album_dir.name

    need = sum(p.stat().st_size for p in files)
    free = _free(card_root)
    if free and need > free:
        raise RuntimeError(f"Kartta yeterli yer yok: {human_size(need)} gerekli, {human_size(free)} boş.")

    dest.mkdir(parents=True, exist_ok=True)
    log(f"Karta kopyalanıyor: {dest}")
    for i, src in enumerate(files, 1):
        if is_cancelled():
            raise Cancelled()
        target = dest / src.name
        if target.exists() and target.stat().st_size == src.stat().st_size:
            log(f"= Zaten var: {src.name}")
        else:
            progress((i - 1) / len(files), f"Kopyalanıyor [{i}/{len(files)}] {src.name}")
            # copyfile: FAT32'de izin/tarih kopyalama hatalarını önler
            shutil.copyfile(src, target)
            log(f"→ {src.name}")
    progress(1.0, "Kopyalama tamamlandı")
    return dest
