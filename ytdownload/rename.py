"""Şarkı adlarını kısaltma (küçük ekranlı / tuşlu telefonlar için).

Örnek: "04 - Gripin- Durma Yağmur Durma (piyano cover)- İlayda Su Çakıroğlu.mp3"
     → "04 Durma Yağmur Durma.mp3"   (şarkı adı etiketi: "Durma Yağmur Durma")

Telefonlar ekranda ya dosya adını ya da dosyanın içindeki "şarkı adı" etiketini
gösterir; bu yüzden ikisi birlikte değiştirilir.
"""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from . import core


@dataclass
class RenameOptions:
    max_len: int = 24  # uzantı hariç dosya adının en fazla uzunluğu (numara dahil)
    number: bool = False  # başa parça numarası koy ("04 Şarkı")
    ascii_only: bool = False  # ğ→g, ş→s ... (Türkçe harfleri gösteremeyen telefonlar için)


# --------------------------------------------------------------------------- #
# Metin temizleme
# --------------------------------------------------------------------------- #
_TR_ASCII = str.maketrans("ğĞüÜşŞıİöÖçÇâÂîÎûÛ", "gGuUsSiIoOcCaAiIuU")

# Parçalar arası ayraç: "A - B", "A- B", "A -B", "A | B", "A – B" ("Hip-Hop" bölünmez)
_SEP = re.compile(r"\s+[-–—|]+\s*|\s*[-–—|]+\s+|\s*//\s*")
_BRACKETS = re.compile(r"\([^()]*\)|\[[^\[\]]*\]|\{[^{}]*\}|【[^】]*】|「[^」]*」|『[^』]*』")
_FEAT = re.compile(r"\s+(?:feat\.?|ft\.?|featuring|eşliğinde)\s+.*$", re.I)
_HASHTAG = re.compile(r"#\S+")

# Tek başına anlam taşımayan kelimeler (karşılaştırma için ASCII, küçük harf)
_NOISE = {
    "official", "officiel", "video", "videoclip", "clip", "klip", "klibi", "audio", "lyric", "lyrics",
    "sozleri", "sozler", "sarki", "music", "muzik", "hd", "hq", "4k", "1080p", "720p", "remaster",
    "remastered", "live", "canli", "version", "versiyon", "edit", "radio", "cover", "covers", "piano",
    "piyano", "akustik", "acoustic", "resmi", "visualizer", "full", "album", "single", "mv", "ost",
    "performance", "performans", "studio", "studyo", "session", "by", "ft", "feat", "prod", "new",
    "yeni", "gitar", "guitar", "enstrumantal", "instrumental", "karaoke", "slowed", "reverb", "speed",
    "up", "sped", "and", "ve", "ile", "tiktok", "trend", "orijinal", "original", "mix", "kayit",
}
# Bu kelimelerle başlayan parçalar ek bilgidir ("Remastered 2011", "Live at ...", "From 'Film'")
_INFO_PREFIX = re.compile(r"^(?:from|live|recorded|remaster(?:ed)?|bonus|taken from|version|edit|canli)\b", re.I)
# Şarkı adının sonunda kalan ek bilgi kelimeleri ("Git Akustik" → "Git")
_TRAILING_NOISE = {"cover", "piano", "piyano", "akustik", "acoustic", "official", "video", "audio",
                   "lyrics", "lyric", "klip", "canli", "live", "version", "versiyon", "hd", "hq", "4k", "resmi"}


def _fold(text: str) -> str:
    """Karşılaştırma için: Türkçe harfleri sadeleştir, küçült, harf/rakam dışını boşluk yap."""
    text = unicodedata.normalize("NFKD", text.translate(_TR_ASCII))
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def to_ascii(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.translate(_TR_ASCII))
    return "".join(c for c in text if not unicodedata.combining(c) and ord(c) < 128)


def _keep_char(c: str) -> bool:
    cat = unicodedata.category(c)
    return cat[0] in "LNM" or c in " '&!?,.+-–—|/"


def _is_filler(segment: str, artists: Iterable[str]) -> bool:
    """Parça sadece sanatçı adı ve/veya ek bilgi kelimelerinden mi oluşuyor?"""
    if _INFO_PREFIX.match(segment.strip()):
        return True
    rest = f" {_fold(segment)} "
    for a in artists:
        fa = _fold(a)
        if fa:
            rest = rest.replace(f" {fa} ", " ")
    words = [w for w in rest.split() if w not in _NOISE and not w.isdigit()]
    return not words


def short_title(title: str, artists: Iterable[str] = (), max_len: int = 40, ascii_only: bool = False) -> str:
    """Uzun YouTube başlığından kısa şarkı adı üretir."""
    artists = [a for a in artists if a and a.strip()]
    original = (title or "").strip()
    t = _HASHTAG.sub(" ", original)
    for _ in range(3):  # iç içe parantezler için
        t = _BRACKETS.sub(" ", t)
    t = re.sub(r"[“”\"‘’«»]", "", t)
    t = "".join(c if _keep_char(c) else " " for c in t)

    segments = [s.strip(" '&,.+/") for s in _SEP.split(t)]
    segments = [_FEAT.sub("", s).strip() for s in segments if s.strip(" '&,.+/")]
    kept = [s for s in segments if not _is_filler(s, artists)]
    if not kept:
        kept = segments or [original]
    # "Sanatçı - Şarkı" en yaygın düzen: birden çok parça kaldıysa ikincisini al
    name = kept[1] if len(kept) >= 2 else kept[0]

    words = name.split()
    while len(words) > 1 and _fold(words[-1]) in _TRAILING_NOISE:
        words.pop()
    # Ayraçsız yapışık sanatçı adı: "Helin Çelik Mutlu Yıllar" → "Mutlu Yıllar"
    for a in sorted({_fold(a) for a in artists if _fold(a)}, key=len, reverse=True):
        k = len(a.split())
        if len(words) > k and _fold(" ".join(words[:k])) == a:
            words = words[k:]
        elif len(words) > k and _fold(" ".join(words[-k:])) == a:
            words = words[:-k]
    name = " ".join(words).strip(" -–—|&,.+/'")
    if ascii_only:
        name = to_ascii(name)
    name = core._BAD_CHARS.sub("", name)
    name = " ".join(name.split()) or core._BAD_CHARS.sub("", original)[:max_len]

    if len(name) > max_len:
        cut = name[:max_len]
        space = cut.rfind(" ")
        if space >= max_len * 0.6:  # kelimeyi bölmeden kes
            cut = cut[:space]
        name = cut.rstrip(" -–—|&,.+/'")
    return name.strip() or "Sarki"


# --------------------------------------------------------------------------- #
# Etiketler
# --------------------------------------------------------------------------- #
def _read_track_number(path: Path) -> int | None:
    try:
        import mutagen

        f = mutagen.File(path)
        tags = f.tags if f else None
        if tags is None:
            return None
        if hasattr(tags, "getall"):  # MP3
            frames = tags.getall("TRCK")
            value = str(frames[0].text[0]) if frames else ""
        else:
            trkn = tags.get("trkn")
            if trkn:
                return int(trkn[0][0]) or None
            value = str((tags.get("tracknumber") or tags.get("TRACKNUMBER") or [""])[0])
        m = re.match(r"\s*(\d+)", value)
        return int(m.group(1)) if m else None
    except Exception:
        return None


def write_title(path: Path, title: str):
    """Dosyanın içindeki şarkı adı etiketini değiştirir (MP3'te ID3v2.3 + ID3v1).

    İlk değişiklikte orijinal (uzun) ad ORIGINAL_TITLE etiketinde saklanır; tekrar
    indirme kontrolü şarkıyı bu sayede kısaltılmış adıyla da tanır.
    """
    ext = path.suffix.lower()
    if ext == ".mp3":
        from mutagen.id3 import ID3, TIT2, TXXX, ID3NoHeaderError

        try:
            tags = ID3(path)
        except ID3NoHeaderError:
            tags = ID3()
        old = tags.getall("TIT2")
        if old and old[0].text and not tags.getall(f"TXXX:{core.ORIGINAL_TITLE}"):
            tags.add(TXXX(encoding=1, desc=core.ORIGINAL_TITLE, text=[str(old[0].text[0])]))
        tags.setall("TIT2", [TIT2(encoding=1, text=[title])])  # UTF-16: eski cihazlar da okur
        # v2.3 en uyumlu sürüm; v1=1 varsa eski tip (ID3v1) etiketi de günceller
        tags.save(path, v1=1, v2_version=3)
        _ascii_id3v1(path, tags)
    elif ext == ".m4a":
        from mutagen.mp4 import MP4

        f = MP4(path)
        if f.tags is None:
            f.add_tags()
        key = f"----:com.apple.iTunes:{core.ORIGINAL_TITLE}"
        if f.tags.get("\xa9nam") and key not in f.tags:
            f.tags[key] = [str(f.tags["\xa9nam"][0]).encode("utf-8")]
        f.tags["\xa9nam"] = [title]
        f.save()
    else:
        import mutagen

        f = mutagen.File(path, easy=True)
        if f is not None:
            f["title"] = [title]
            f.save()


def _ascii_id3v1(path: Path, tags):
    """ID3v1 sadece Latin-1 destekler: "Yağmur" → "Ya?mur" olur. Bunun yerine
    Türkçe harfleri sadeleştirip yaz ("Yagmur"); bazı tuşlu telefonlar sadece bunu okur."""
    def text(frame_id):
        frames = tags.getall(frame_id)
        return to_ascii(str(frames[0].text[0])) if frames and frames[0].text else ""

    with open(path, "r+b") as f:
        f.seek(0, os.SEEK_END)
        if f.tell() < 128:
            return
        f.seek(-128, os.SEEK_END)
        if f.read(3) != b"TAG":
            return
        for value in (text("TIT2"), text("TPE1"), text("TALB")):  # başlık, sanatçı, albüm (30'ar bayt)
            f.write(value.encode("ascii", "ignore")[:30].ljust(30, b"\0"))


def artists_from_info(info: dict) -> list[str]:
    names = list(info.get("artists") or [])
    for key in ("artist", "creator", "uploader", "channel", "album_artist"):
        if info.get(key):
            names.append(str(info[key]))
    return [re.sub(r"\s*-\s*topic$", "", n, flags=re.I) for n in names]


def _split_artists(artist: str | None) -> list[str]:
    if not artist:
        return []
    parts = re.split(r",|&| feat\.? | ft\.? | x | ve ", artist, flags=re.I)
    return [artist, *(p.strip() for p in parts if p.strip())]


# --------------------------------------------------------------------------- #
# Yeniden adlandırma planı
# --------------------------------------------------------------------------- #
@dataclass
class RenamePlan:
    path: Path  # şimdiki dosya
    new_name: str  # yeni dosya adı (uzantılı)
    old_title: str | None  # geri almak için eski etiket
    new_title: str

    @property
    def changed(self) -> bool:
        return self.new_name != self.path.name or (self.old_title or "") != self.new_title


_NUM_PREFIX = re.compile(r"^(\d{1,3})(?:\s*[-._)]\s*|\s+)(?=\S)")


def plan_file(path: Path, opts: RenameOptions, info: dict | None = None, number: int | None = None) -> RenamePlan:
    artist, tag_title, _ = core._read_tags(path)
    file_num = None
    m = _NUM_PREFIX.match(path.stem)
    stem_title = path.stem[m.end():] if m else path.stem
    if m:
        file_num = int(m.group(1))
    number = number or _read_track_number(path) or file_num

    if info:
        source = info.get("track") or info.get("title") or tag_title or stem_title
        artists = artists_from_info(info)
    else:
        # Daha önce kısaltıldıysa saklanan orijinal başlıktan yeniden hesapla (ayar değişince
        # kesilen kelimeler geri gelebilsin); sonuç her çalıştırmada aynı olur
        source = core._read_original_title(path) or tag_title or stem_title
        artists = _split_artists(artist)
    prefix = f"{number:02d} " if opts.number and number else ""
    ext = path.suffix.lower()
    limit = max(8, opts.max_len - len(prefix))
    short = short_title(source, artists, max_len=limit, ascii_only=opts.ascii_only)
    return RenamePlan(path, f"{prefix}{short}{ext}", tag_title, short)


def _audio_files(folder: Path) -> list[Path]:
    files = []
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d != "System Volume Information")
        for name in sorted(filenames):
            if not name.startswith(".") and Path(name).suffix.lower() in core.AUDIO_EXTS:
                files.append(Path(dirpath) / name)
    return files


def plan_folder(folder: str | Path, opts: RenameOptions,
                progress: Callable[[int, int], None] = lambda i, n: None) -> list[RenamePlan]:
    """Klasördeki (alt klasörler dahil) tüm şarkılar için kısaltma planı çıkarır."""
    files = _audio_files(Path(folder))
    plans = []
    for i, f in enumerate(files, 1):
        plans.append(plan_file(f, opts))
        progress(i, len(files))
    make_unique(plans)
    return plans


def make_unique(plans: list[RenamePlan]):
    """Aynı klasörde iki dosyanın aynı adı almasını önler ("Şarkı", "Şarkı 2")."""
    by_dir: dict[Path, set[str]] = {}
    renaming = {p.path for p in plans}

    def taken_in(folder: Path) -> set[str]:
        if folder not in by_dir:
            by_dir[folder] = {f.name.casefold() for f in folder.iterdir() if f not in renaming}
        return by_dir[folder]

    # 1) Zaten uygun adı olan ("Şarkı.mp3" ya da "Şarkı 2.mp3") dosya adını korur;
    #    böylece aynı adlı iki dosya her çalıştırmada yer değiştirmez.
    pending = []
    for p in plans:
        stem, ext = os.path.splitext(p.new_name)
        current = p.path.name
        fits = current == p.new_name or re.fullmatch(re.escape(stem) + r" \d+" + re.escape(ext), current)
        taken = taken_in(p.path.parent)
        if fits and current.casefold() not in taken:
            p.new_name = current
            taken.add(current.casefold())
        else:
            pending.append(p)
    # 2) Kalanlara boş ad ver
    for p in pending:
        taken = taken_in(p.path.parent)
        stem, ext = os.path.splitext(p.new_name)
        name, n = p.new_name, 2
        while name.casefold() in taken:
            name = f"{stem} {n}{ext}"
            n += 1
        p.new_name = name
        taken.add(name.casefold())


def apply_plan(plan: RenamePlan) -> Path:
    """Etiketi günceller ve dosyayı yeniden adlandırır; yeni yolu döndürür."""
    path = plan.path
    if (plan.old_title or "") != plan.new_title:
        write_title(path, plan.new_title)
    target = path.with_name(plan.new_name)
    if target.name != path.name:
        if target.name.casefold() == path.name.casefold():
            # Sadece büyük/küçük harf farkı: bazı dosya sistemlerinde ara ad gerekir
            tmp = path.with_name(path.name + ".tmp_rename")
            os.replace(path, tmp)
            os.replace(tmp, target)
        else:
            if target.exists():
                raise FileExistsError(f"Bu adda dosya zaten var: {target.name}")
            os.replace(path, target)
    return target


def apply_plans(plans: list[RenamePlan], progress: Callable[[int, int], None] = lambda i, n: None
                ) -> tuple[list[tuple[RenamePlan, Path]], list[str]]:
    """Birden çok planı güvenle uygular: önce geçici adlara, sonra asıl adlara taşır.
    Böylece "A → B, B → A" gibi zincirlerde dosyalar birbirinin üstüne yazılmaz."""
    done, errors, staged = [], [], []
    for i, p in enumerate(plans, 1):
        try:
            if (p.old_title or "") != p.new_title:
                write_title(p.path, p.new_title)
            if p.new_name != p.path.name:
                tmp = p.path.with_name(f"{p.path.name}.{i}.tmp_rename")
                os.replace(p.path, tmp)
                staged.append((p, tmp))
            else:
                done.append((p, p.path))
        except Exception as e:  # noqa: BLE001
            errors.append(f"{p.path.name}: {e}")
        progress(i, len(plans) * 2)
    for j, (p, tmp) in enumerate(staged, 1):
        target = p.path.with_name(p.new_name)
        try:
            if target.exists():
                raise FileExistsError(f"Bu adda dosya zaten var: {target.name}")
            os.replace(tmp, target)
            done.append((p, target))
        except Exception as e:  # noqa: BLE001
            os.replace(tmp, p.path)  # asıl adına geri koy
            errors.append(f"{p.path.name}: {e}")
        progress(len(plans) + j, len(plans) * 2)
    return done, errors


def undo_plan(plan: RenamePlan, new_path: Path):
    """apply_plan'ı geri alır."""
    if new_path.name != plan.path.name and new_path.exists():
        os.replace(new_path, plan.path)
    if plan.old_title is not None and plan.old_title != plan.new_title:
        write_title(plan.path, plan.old_title)


def shorten_downloaded(path: Path, info: dict, number: int, opts: RenameOptions) -> Path:
    """İndirme sırasında: yeni inen şarkının adını ve etiketini kısaltır."""
    plan = plan_file(path, opts, info=info, number=number)
    make_unique([plan])
    return apply_plan(plan) if plan.changed else path


def _main():
    """Deneme: python -m ytdownload.rename "<klasör>" [en fazla harf]
    Hiçbir dosyayı değiştirmez, sadece eski → yeni adları yazar."""
    import sys

    if len(sys.argv) < 2:
        print(_main.__doc__)
        return
    opts = RenameOptions(max_len=int(sys.argv[2]) if len(sys.argv) > 2 else 24)
    for p in plan_folder(sys.argv[1], opts):
        mark = "→" if p.changed else "="
        artist = core._read_tags(p.path)[0]
        original = core._read_original_title(p.path) or p.old_title
        print(f"{p.path.name}\n    {mark} {p.new_name}   [orijinal: {original!r} | sanatçı: {artist!r}]")


if __name__ == "__main__":
    _main()
