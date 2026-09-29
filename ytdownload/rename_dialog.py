"""Mevcut şarkıların adlarını kısaltma penceresi (önizleme, elle düzenleme, geri alma)."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from . import core, rename


class RenameDialog(tk.Toplevel):
    def __init__(self, master, folder: str, max_len: tk.IntVar, number: tk.BooleanVar, ascii_only: tk.BooleanVar):
        super().__init__(master)
        self.title("Şarkı adlarını kısalt")
        self.geometry("820x520")
        self.minsize(640, 400)
        self.transient(master)

        self.folder_var = tk.StringVar(value=folder)
        self.max_len, self.number, self.ascii_only = max_len, number, ascii_only
        self.status_var = tk.StringVar(value="Klasörü seçip 'Önizle'ye basın.")
        self._plans: dict[str, rename.RenamePlan] = {}  # treeview satırı → plan
        self._undo: list[tuple[rename.RenamePlan, Path]] = []
        self._events: queue.Queue = queue.Queue()
        self._busy = False

        self._build()
        self.after(100, self._poll)
        if folder and Path(folder).is_dir():
            self.preview()

    # ------------------------------------------------------------------ UI --
    def _build(self):
        pad = {"padx": 6, "pady": 4}
        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")
        ttk.Label(top, text="Klasör:").pack(side="left")
        ttk.Entry(top, textvariable=self.folder_var).pack(side="left", fill="x", expand=True, **pad)
        ttk.Button(top, text="Seç...", command=self._choose).pack(side="left")

        opts = ttk.Frame(self, padding=(8, 0))
        opts.pack(fill="x")
        ttk.Label(opts, text="En fazla").pack(side="left")
        ttk.Spinbox(opts, from_=10, to=60, textvariable=self.max_len, width=4).pack(side="left", padx=4)
        ttk.Label(opts, text="harf").pack(side="left")
        ttk.Checkbutton(opts, text="Başa sıra numarası", variable=self.number).pack(side="left", padx=10)
        ttk.Checkbutton(opts, text="Türkçe harfleri sadeleştir (ğ→g)", variable=self.ascii_only).pack(side="left")
        ttk.Button(opts, text="Önizle", command=self.preview).pack(side="right")

        mid = ttk.Frame(self, padding=8)
        mid.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(mid, columns=("old", "new"), show="headings", selectmode="extended")
        self.tree.heading("old", text="Şimdiki ad")
        self.tree.heading("new", text="Yeni ad  (değiştirmek için çift tıklayın)")
        self.tree.column("old", width=420)
        self.tree.column("new", width=300)
        sb = ttk.Scrollbar(mid, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", self._edit)
        self.tree.bind("<Delete>", lambda e: self._skip())
        self.tree.bind("<BackSpace>", lambda e: self._skip())

        bottom = ttk.Frame(self, padding=8)
        bottom.pack(fill="x")
        ttk.Label(bottom, textvariable=self.status_var).pack(side="left")
        ttk.Button(bottom, text="Kapat", command=self.destroy).pack(side="right")
        self.undo_btn = ttk.Button(bottom, text="Geri Al", command=self.undo, state="disabled")
        self.undo_btn.pack(side="right", padx=4)
        self.apply_btn = ttk.Button(bottom, text="✓ Uygula", command=self.apply, state="disabled")
        self.apply_btn.pack(side="right", padx=4)
        ttk.Button(bottom, text="Seçilenleri atla", command=self._skip).pack(side="right", padx=4)

    def _choose(self):
        d = filedialog.askdirectory(parent=self, initialdir=self.folder_var.get() or str(Path.home()))
        if d:
            self.folder_var.set(d)
            self.preview()

    def _opts(self) -> rename.RenameOptions:
        try:
            max_len = max(10, min(60, int(self.max_len.get())))
        except (tk.TclError, ValueError):
            max_len = 24
        return rename.RenameOptions(max_len=max_len, number=self.number.get(), ascii_only=self.ascii_only.get())

    # ------------------------------------------------------------- actions --
    def preview(self):
        folder = self.folder_var.get()
        if self._busy:
            return
        if not Path(folder).is_dir():
            messagebox.showwarning("Klasör yok", "Lütfen şarkıların bulunduğu klasörü seçin.", parent=self)
            return
        self._busy = True
        self.apply_btn.configure(state="disabled")
        self.status_var.set("Şarkılar okunuyor...")
        opts = self._opts()

        def work():
            try:
                plans = rename.plan_folder(
                    folder, opts, progress=lambda i, n: self._events.put(("status", f"Okunuyor {i}/{n}...")))
                self._events.put(("plans", plans))
            except Exception as e:  # noqa: BLE001
                self._events.put(("error", str(e)))

        threading.Thread(target=work, daemon=True).start()

    def _show(self, plans: list[rename.RenamePlan]):
        self.tree.delete(*self.tree.get_children())
        self._plans.clear()
        base = Path(self.folder_var.get())
        changed = [p for p in plans if p.changed]
        for p in changed:
            rel = p.path.relative_to(base)
            old = str(rel.parent / p.path.name) if str(rel.parent) != "." else p.path.name
            iid = self.tree.insert("", "end", values=(old, p.new_name))
            self._plans[iid] = p
        same = len(plans) - len(changed)
        self.status_var.set(f"{len(changed)} şarkının adı kısaltılacak" + (f", {same} şarkı zaten kısa." if same else "."))
        self.apply_btn.configure(state="normal" if changed else "disabled")

    def _edit(self, event):
        iid = self.tree.identify_row(event.y)
        plan = self._plans.get(iid)
        if not plan:
            return
        stem = Path(plan.new_name).stem
        new = simpledialog.askstring("Yeni ad", f"{plan.path.name}\n\nYeni ad (uzantısız):",
                                     initialvalue=stem, parent=self)
        if new is None:
            return
        new = core._BAD_CHARS.sub("", new).strip().rstrip(".")
        if not new:
            return
        plan.new_name = new + plan.path.suffix.lower()
        # Etikete numarasız hali yaz ("04 Şarkı" → "Şarkı")
        m = rename._NUM_PREFIX.match(new)
        plan.new_title = new[m.end():] if m else new
        self.tree.set(iid, "new", plan.new_name)

    def _skip(self):
        for iid in self.tree.selection():
            self._plans.pop(iid, None)
            self.tree.delete(iid)
        self.status_var.set(f"{len(self._plans)} şarkının adı kısaltılacak.")
        if not self._plans:
            self.apply_btn.configure(state="disabled")

    def apply(self):
        plans = list(self._plans.values())
        if not plans or self._busy:
            return
        rename.make_unique(plans)  # elle düzenlenenler çakışmasın
        if not messagebox.askyesno("Onay", f"{len(plans)} şarkının adı ve şarkı adı etiketi değiştirilecek.\n"
                                           "İsterseniz sonra 'Geri Al' ile geri alabilirsiniz.\n\nDevam edilsin mi?",
                                   parent=self):
            return
        self._busy = True
        self.apply_btn.configure(state="disabled")
        folder = Path(self.folder_var.get())

        def work():
            done, errors = rename.apply_plans(
                plans, progress=lambda i, n: self._events.put(("status", f"Değiştiriliyor {i}/{n}...")))
            core.clean_mac_junk(folder)
            self._events.put(("applied", (done, errors)))

        threading.Thread(target=work, daemon=True).start()

    def undo(self):
        if not self._undo or self._busy:
            return
        self._busy = True
        items = list(reversed(self._undo))

        def work():
            errors = []
            for p, new_path in items:
                try:
                    rename.undo_plan(p, new_path)
                except Exception as e:  # noqa: BLE001
                    errors.append(f"{new_path.name}: {e}")
            core.clean_mac_junk(Path(self.folder_var.get()))
            self._events.put(("undone", errors))

        threading.Thread(target=work, daemon=True).start()

    def _poll(self):
        try:
            while True:
                kind, data = self._events.get_nowait()
                if kind == "status":
                    self.status_var.set(data)
                elif kind == "plans":
                    self._busy = False
                    self._show(data)
                elif kind == "applied":
                    done, errors = data
                    self._busy = False
                    self._undo = done
                    self.undo_btn.configure(state="normal" if done else "disabled")
                    self.tree.delete(*self.tree.get_children())
                    self._plans.clear()
                    self.status_var.set(f"{len(done)} şarkının adı kısaltıldı.")
                    if errors:
                        messagebox.showwarning("Bazı dosyalar değiştirilemedi", "\n".join(errors[:15]), parent=self)
                elif kind == "undone":
                    self._busy = False
                    self._undo = []
                    self.undo_btn.configure(state="disabled")
                    self.status_var.set("Geri alındı.")
                    if data:
                        messagebox.showwarning("Bazı dosyalar geri alınamadı", "\n".join(data[:15]), parent=self)
                    self.preview()
                elif kind == "error":
                    self._busy = False
                    self.status_var.set("Hata")
                    messagebox.showerror("Hata", data, parent=self)
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(100, self._poll)
