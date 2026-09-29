"""Tkinter arayüzü: YouTube albümünü indir ve microSD karta aktar."""

from __future__ import annotations

import os
import platform
import queue
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import core, rename
from .rename_dialog import RenameDialog

DEFAULT_DOWNLOAD_DIR = Path.home() / "Music" / "YTDownload"
# Görünen ad → core.download_album audio_format
FORMATS = ["m4a (orijinal kalite)", "mp3"]


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("YouTube Albüm → microSD")
        self.minsize(560, 480)

        self._events: queue.Queue = queue.Queue()
        self._cancel = threading.Event()
        self._worker: threading.Thread | None = None
        self._drives: list[core.Drive] = []

        self.url_var = tk.StringVar()
        self.format_var = tk.StringVar(value=FORMATS[0])
        self.quality_var = tk.StringVar(value="192")
        self.dl_dir_var = tk.StringVar(value=str(DEFAULT_DOWNLOAD_DIR))
        self.drive_var = tk.StringVar()
        self.subfolder_var = tk.StringVar(value="Music")
        self.copy_var = tk.BooleanVar(value=True)
        self.workers_var = tk.IntVar(value=3)
        self.shorten_var = tk.BooleanVar(value=False)
        self.maxlen_var = tk.IntVar(value=24)
        self.number_var = tk.BooleanVar(value=True)
        self.ascii_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="Hazır")

        self._build()
        self.refresh_drives()
        self.after(100, self._poll)

    # ------------------------------------------------------------------ UI --
    def _build(self):
        pad = {"padx": 8, "pady": 4}
        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)
        root.columnconfigure(1, weight=1)

        ttk.Label(root, text="Albüm / Playlist linki:").grid(row=0, column=0, sticky="w", **pad)
        url_entry = ttk.Entry(root, textvariable=self.url_var)
        url_entry.grid(row=0, column=1, columnspan=2, sticky="ew", **pad)
        url_entry.focus()
        ttk.Button(root, text="Yapıştır", command=self._paste).grid(row=0, column=3, **pad)

        ttk.Label(root, text="Format / Kalite:").grid(row=1, column=0, sticky="w", **pad)
        fmt = ttk.Frame(root)
        fmt.grid(row=1, column=1, columnspan=3, sticky="w", **pad)
        fmt_combo = ttk.Combobox(fmt, textvariable=self.format_var, values=FORMATS, width=20, state="readonly")
        fmt_combo.pack(side="left")
        fmt_combo.bind("<<ComboboxSelected>>", lambda e: self._toggle_quality())
        self.quality_combo = ttk.Combobox(fmt, textvariable=self.quality_var, values=["128", "192", "256", "320"],
                                          width=6, state="readonly")
        self.quality_combo.pack(side="left", padx=6)
        self.quality_label = ttk.Label(fmt, text="kbps")
        self.quality_label.pack(side="left")
        ttk.Label(fmt, text="   Aynı anda:").pack(side="left")
        ttk.Spinbox(fmt, from_=1, to=6, textvariable=self.workers_var, width=3, state="readonly").pack(side="left", padx=4)
        ttk.Label(fmt, text="şarkı").pack(side="left")
        self._toggle_quality()

        ttk.Label(root, text="Şarkı adları:").grid(row=2, column=0, sticky="w", **pad)
        names = ttk.Frame(root)
        names.grid(row=2, column=1, columnspan=3, sticky="w", **pad)
        ttk.Checkbutton(names, text="Kısalt (tuşlu telefonlar için), en fazla", variable=self.shorten_var).pack(side="left")
        ttk.Spinbox(names, from_=10, to=60, textvariable=self.maxlen_var, width=4).pack(side="left", padx=4)
        ttk.Label(names, text="harf").pack(side="left")
        ttk.Checkbutton(names, text="Numara", variable=self.number_var).pack(side="left", padx=(10, 0))
        ttk.Checkbutton(names, text="ğ→g", variable=self.ascii_var).pack(side="left", padx=(10, 0))

        ttk.Label(root, text="Bilgisayarda kayıt:").grid(row=3, column=0, sticky="w", **pad)
        ttk.Entry(root, textvariable=self.dl_dir_var).grid(row=3, column=1, columnspan=2, sticky="ew", **pad)
        ttk.Button(root, text="Seç...", command=self._choose_dl_dir).grid(row=3, column=3, **pad)

        ttk.Checkbutton(root, text="İndirdikten sonra microSD karta kopyala", variable=self.copy_var,
                        command=self._toggle_copy).grid(row=4, column=0, columnspan=4, sticky="w", **pad)

        ttk.Label(root, text="microSD kart:").grid(row=5, column=0, sticky="w", **pad)
        self.drive_combo = ttk.Combobox(root, textvariable=self.drive_var, state="readonly")
        self.drive_combo.grid(row=5, column=1, sticky="ew", **pad)
        self.refresh_btn = ttk.Button(root, text="Yenile", command=self.refresh_drives)
        self.refresh_btn.grid(row=5, column=2, **pad)
        self.browse_btn = ttk.Button(root, text="Klasör...", command=self._choose_card_dir)
        self.browse_btn.grid(row=5, column=3, **pad)

        ttk.Label(root, text="Karttaki klasör:").grid(row=6, column=0, sticky="w", **pad)
        self.subfolder_entry = ttk.Entry(root, textvariable=self.subfolder_var)
        self.subfolder_entry.grid(row=6, column=1, sticky="ew", **pad)
        ttk.Label(root, text="/ <Albüm adı>/").grid(row=6, column=2, columnspan=2, sticky="w", **pad)

        btns = ttk.Frame(root)
        btns.grid(row=7, column=0, columnspan=4, sticky="ew", pady=(10, 4))
        self.start_btn = ttk.Button(btns, text="▶  İndir ve Aktar", command=self.start)
        self.start_btn.pack(side="left", padx=8)
        self.cancel_btn = ttk.Button(btns, text="İptal", command=self.cancel, state="disabled")
        self.cancel_btn.pack(side="left")
        ttk.Button(btns, text="Klasörü Aç", command=self._open_dl_dir).pack(side="right", padx=8)
        ttk.Button(btns, text="Şarkı adlarını kısalt...", command=self._open_rename).pack(side="right")

        self.progress = ttk.Progressbar(root, maximum=1.0)
        self.progress.grid(row=8, column=0, columnspan=4, sticky="ew", **pad)
        ttk.Label(root, textvariable=self.status_var).grid(row=9, column=0, columnspan=4, sticky="w", **pad)

        log_frame = ttk.Frame(root)
        log_frame.grid(row=10, column=0, columnspan=4, sticky="nsew", **pad)
        root.rowconfigure(10, weight=1)
        self.log_text = tk.Text(log_frame, height=10, state="disabled", wrap="word")
        scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

    # ------------------------------------------------------------- helpers --
    def _paste(self):
        try:
            self.url_var.set(self.clipboard_get().strip())
        except tk.TclError:
            pass

    def _choose_dl_dir(self):
        d = filedialog.askdirectory(initialdir=self.dl_dir_var.get() or str(Path.home()))
        if d:
            self.dl_dir_var.set(d)

    def _choose_card_dir(self):
        d = filedialog.askdirectory(title="microSD kartı / hedef klasörü seçin")
        if d:
            drive = core.Drive(d, d, core._free(d))
            self._drives.append(drive)
            self.drive_combo["values"] = [x.display() for x in self._drives]
            self.drive_combo.current(len(self._drives) - 1)

    def _open_dl_dir(self):
        path = self.dl_dir_var.get()
        os.makedirs(path, exist_ok=True)
        system = platform.system()
        if system == "Windows":
            os.startfile(path)  # type: ignore[attr-defined]
        elif system == "Darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])

    def _rename_opts(self) -> rename.RenameOptions:
        try:
            max_len = max(10, min(60, int(self.maxlen_var.get())))
        except (tk.TclError, ValueError):
            max_len = 24
        return rename.RenameOptions(max_len=max_len, number=self.number_var.get(), ascii_only=self.ascii_var.get())

    def _open_rename(self):
        # Varsayılan: seçili karttaki müzik klasörü, yoksa bilgisayardaki indirme klasörü
        folder = self.dl_dir_var.get()
        drive = self._selected_drive() if self.copy_var.get() else None
        if drive:
            music = Path(drive.path) / self.subfolder_var.get().strip()
            folder = str(music if self.subfolder_var.get().strip() and music.is_dir() else Path(drive.path))
        RenameDialog(self, folder, self.maxlen_var, self.number_var, self.ascii_var)

    def _audio_format(self) -> str:
        return self.format_var.get().split()[0]

    def _toggle_quality(self):
        # m4a'da ses dönüştürülmediği için kalite ayarı anlamsız
        is_mp3 = self._audio_format() == "mp3"
        self.quality_combo.configure(state="readonly" if is_mp3 else "disabled")
        self.quality_label.configure(text="kbps" if is_mp3 else "kbps (m4a'da dönüştürme yapılmaz)")

    def _toggle_copy(self):
        state = "normal" if self.copy_var.get() else "disabled"
        self.drive_combo.configure(state="readonly" if state == "normal" else "disabled")
        for w in (self.refresh_btn, self.browse_btn, self.subfolder_entry):
            w.configure(state=state)

    def refresh_drives(self):
        self._drives = core.list_removable_drives()
        self.drive_combo["values"] = [d.display() for d in self._drives]
        if self._drives:
            self.drive_combo.current(0)
            self.log(f"{len(self._drives)} çıkarılabilir sürücü bulundu.")
        else:
            self.drive_var.set("")
            self.log("microSD kart bulunamadı. Kartı takıp 'Yenile'ye basın veya 'Klasör...' ile seçin.")

    def _selected_drive(self) -> core.Drive | None:
        idx = self.drive_combo.current()
        return self._drives[idx] if 0 <= idx < len(self._drives) else None

    def log(self, msg: str):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", msg + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _set_running(self, running: bool):
        self.start_btn.configure(state="disabled" if running else "normal")
        self.cancel_btn.configure(state="normal" if running else "disabled")

    # --------------------------------------------------------------- work --
    def start(self):
        url = self.url_var.get().strip()
        if not url.startswith(("http://", "https://")):
            messagebox.showwarning("Link gerekli", "Lütfen bir YouTube / YouTube Music albüm veya playlist linki girin.")
            return
        drive = self._selected_drive() if self.copy_var.get() else None
        if self.copy_var.get() and drive is None:
            messagebox.showwarning("Kart seçilmedi", "Lütfen microSD kartı seçin ya da kopyalama seçeneğini kapatın.")
            return

        self._cancel.clear()
        self._set_running(True)
        self.progress["value"] = 0
        args = (url, Path(self.dl_dir_var.get()), self._audio_format(), self.quality_var.get(),
                drive.path if drive else None, self.subfolder_var.get(), self.workers_var.get(),
                self._rename_opts() if self.shorten_var.get() else None)
        self._worker = threading.Thread(target=self._run, args=args, daemon=True)
        self._worker.start()

    def cancel(self):
        self._cancel.set()
        self.status_var.set("İptal ediliyor...")

    def _run(self, url, dl_dir, fmt, quality, card, subfolder, workers, rename_opts):
        emit = self._events.put
        log = lambda m: emit(("log", m))
        try:
            # Her şarkı iner inmez karta kopyalanır (indirme ve kopyalama aynı anda)
            res = core.download_album(
                url, dl_dir, fmt, quality, card_root=card, sub_folder=subfolder, workers=workers,
                rename_opts=rename_opts, log=log,
                progress=lambda f, s: emit(("progress", f, s)), is_cancelled=self._cancel.is_set)
            msg = f"Tamamlandı!\n\nİndirilen: {res.downloaded}\nZaten vardı (atlandı): {res.skipped}"
            if res.failed:
                msg += f"\nİndirilemeyen: {res.failed} (ayrıntılar kayıt penceresinde)"
            if res.card_dir:
                msg += (f"\nKarta kopyalanan: {res.copied}\n\nKarttaki klasör:\n{res.card_dir}\n\n"
                        "Kartı çıkarmadan önce 'Güvenli Çıkar' yapmayı unutmayın.")
            else:
                msg += f"\n\nKlasör:\n{res.album_dir}"
            emit(("done", msg))
        except core.Cancelled:
            emit(("cancelled", None))
        except Exception as e:  # noqa: BLE001 - kullanıcıya göster
            emit(("error", str(e)))

    def _poll(self):
        try:
            while True:
                kind, *rest = self._events.get_nowait()
                if kind == "log":
                    self.log(rest[0])
                elif kind == "progress":
                    self.progress["value"] = rest[0]
                    self.status_var.set(rest[1])
                elif kind == "done":
                    self.progress["value"] = 1.0
                    self.status_var.set("Tamamlandı")
                    self.log(rest[0])
                    self._set_running(False)
                    self.refresh_drives()
                    messagebox.showinfo("Bitti", rest[0])
                elif kind == "cancelled":
                    self.status_var.set("İptal edildi")
                    self.log("İşlem iptal edildi.")
                    self._set_running(False)
                elif kind == "error":
                    self.status_var.set("Hata")
                    self.log(f"HATA: {rest[0]}")
                    self._set_running(False)
                    messagebox.showerror("Hata", rest[0])
        except queue.Empty:
            pass
        self.after(100, self._poll)


def main():
    App().mainloop()


if __name__ == "__main__":
    main()
