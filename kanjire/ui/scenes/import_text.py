"""Background ingestion of a user-chosen text file into a new corpus deck."""
from __future__ import annotations

import re
import threading
import time
import unicodedata
from datetime import date
from pathlib import Path

import pyglet
from pyglet import shapes
from pyglet.graphics import OrderedGroup
from pyglet.text import Label

from kanjire.data import db, ingest
from kanjire.i18n import tr
from kanjire.ui import theme
from kanjire.ui.fonts import JP_FONT
from kanjire.ui.scene import Scene
from kanjire.ui.widgets.button import Button


def slugify(name: str) -> str:
    name = unicodedata.normalize("NFKC", name).strip().lower()
    name = re.sub(r"[^\w぀-ヿ一-鿿]+", "-", name)
    return name.strip("-") or "corpus"


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "cp932", "shift_jis", "euc_jp"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def open_file_dialog() -> Path | None:
    """Modal native file picker via tkinter. Returns ``None`` on cancel."""
    import tkinter
    from tkinter import filedialog

    root = tkinter.Tk()
    root.withdraw()
    root.update()
    try:
        path = filedialog.askopenfilename(
            title="Choose a Japanese text file",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
    finally:
        try:
            root.destroy()
        except Exception:
            pass
    return Path(path) if path else None


def open_paste_dialog() -> tuple[str, str] | None:
    """Modal paste dialog with a name field. Returns ``(text, name)`` or ``None``.

    Built with tkinter so we get a real native text widget with paste/IME
    support out of the box.
    """
    import tkinter
    from tkinter import scrolledtext

    result: dict[str, str] = {}
    root = tkinter.Tk()
    root.title(tr("PASTE_TITLE"))
    root.geometry("640x520")
    root.minsize(420, 340)

    # Name field
    top = tkinter.Frame(root, padx=12, pady=10)
    top.pack(fill="x")
    tkinter.Label(top, text=tr("PASTE_LABEL")).pack(side="left")
    name_var = tkinter.StringVar(value=tr("PASTE_DEFAULT"))
    name_entry = tkinter.Entry(top, textvariable=name_var, width=32)
    name_entry.pack(side="left", padx=8, fill="x", expand=True)

    # Multi-line text
    middle = tkinter.Frame(root, padx=12)
    middle.pack(fill="both", expand=True)
    tkinter.Label(
        middle,
        text=tr("PASTE_HINT"),
        anchor="w", justify="left", wraplength=600,
    ).pack(fill="x", pady=(0, 6))
    txt = scrolledtext.ScrolledText(
        middle, wrap="word", font=("Yu Gothic UI", 11), undo=True,
    )
    txt.pack(fill="both", expand=True)
    txt.focus_set()

    # Buttons
    bottom = tkinter.Frame(root, padx=12, pady=12)
    bottom.pack(fill="x")

    def on_ok() -> None:
        text = txt.get("1.0", "end").strip()
        name = name_var.get().strip() or tr("PASTE_DEFAULT")
        if text:
            result["text"] = text
            result["name"] = name
        root.destroy()

    def on_cancel() -> None:
        root.destroy()

    tkinter.Button(bottom, text=tr("PASTE_CANCEL"), width=10, command=on_cancel).pack(
        side="right", padx=4
    )
    tkinter.Button(bottom, text=tr("PASTE_OK"), width=10, command=on_ok).pack(side="right")
    root.protocol("WM_DELETE_WINDOW", on_cancel)
    root.bind("<Control-Return>", lambda e: on_ok())
    root.bind("<Escape>", lambda e: on_cancel())

    root.mainloop()
    if "text" in result:
        return result["text"], result["name"]
    return None


class ImportTextScene(Scene):
    """Tokenises the chosen text and ingests it into a new deck.

    Provide *either* ``path`` (a text file on disk) *or* ``raw_text`` (a paste
    from the user).
    """

    def __init__(
        self,
        app,
        *,
        path: Path | None = None,
        raw_text: str | None = None,
        display_name: str = "",
    ) -> None:
        super().__init__(app)
        if path is None and not raw_text:
            raise ValueError("import needs either path or raw_text")
        self.path = path
        self.raw_text = raw_text
        self.display_name = display_name or (path.stem if path else "Pasted text")
        self.deck_name = f"corpus:{slugify(self.display_name)}"

        self.batch = pyglet.graphics.Batch()
        self.g_bg = OrderedGroup(0)
        self.g_text = OrderedGroup(1)
        self.buttons: list[Button] = []

        # Shared state between the worker thread and the UI tick.
        self._lock = threading.Lock()
        self._state = {
            "phase": "reading",
            "done": 0,
            "total": 0,
            "word": "",
            "result": None,
            "error": None,
            "started_at": time.time(),
        }
        self._dismissed = False
        self._done_elapsed = 0.0
        self._build()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="kanjire-ingest")
        self._thread.start()

    # ------------------------------------------------------------------ #
    def _build(self) -> None:
        mk = lambda **kw: Label(
            "", font_name=JP_FONT, batch=self.batch, group=self.g_text,
            anchor_x="center", anchor_y="center", **kw
        )
        self.title = mk(font_size=22, bold=True)
        self.title.text = tr("IMPORT_TITLE")
        self.title.color = theme.with_alpha(theme.TEXT, 255)
        self.subtitle = mk(font_size=15)
        self.subtitle.color = theme.with_alpha(theme.MUTED, 255)
        self.subtitle.text = self.display_name
        self.phase_label = mk(font_size=14)
        self.phase_label.color = theme.with_alpha(theme.ACCENT, 255)
        self.detail_label = mk(font_size=12)
        self.detail_label.color = theme.with_alpha(theme.MUTED, 255)
        self.count_label = mk(font_size=12)
        self.count_label.color = theme.with_alpha(theme.DIM, 255)

        self.bar_bg = shapes.Rectangle(0, 0, 10, 10, color=theme.PANEL_HI,
                                       batch=self.batch, group=self.g_bg)
        self.bar_fg = shapes.Rectangle(0, 0, 10, 10, color=theme.ACCENT,
                                       batch=self.batch, group=self.g_bg)

        self.back_btn = Button(tr("BTN_BACK"), lambda: self.app.go_menu(),
                               self.batch, self.g_bg, self.g_text,
                               accent=theme.ACCENT, font_size=14)
        self.buttons.append(self.back_btn)

    # ------------------------------------------------------------------ #
    def _run(self) -> None:
        try:
            text = _read_text(self.path) if self.path else (self.raw_text or "")
            with self._lock:
                self._state["phase"] = "tokenising"

            word_counts, pos_by_word, reading_by_word, kanji_counts, total = (
                ingest.count_tokens(text)
            )
            with self._lock:
                self._state["phase"] = "resolving"
                self._state["total"] = len(word_counts)

            def progress(done, total, word):
                with self._lock:
                    self._state["done"] = done
                    self._state["total"] = total
                    self._state["word"] = word

            # Use a thread-local Jamdict so its SQLite connections stay bound to
            # this worker thread (avoids noisy errors at interpreter shutdown).
            from jamdict import Jamdict

            jam = Jamdict()
            try:
                words = ingest.resolve_words(
                    word_counts, pos_by_word, reading_by_word, total,
                    jam=jam, progress=progress,
                )
                kanji = ingest.resolve_kanji(kanji_counts, jam=jam)
            finally:
                try:
                    jam.close()
                except Exception:
                    pass
            result = ingest.CorpusResult(
                words=words, kanji=kanji, total_tokens=total,
                candidate_words=len(word_counts), resolved_words=len(words),
            )

            if not words:
                raise RuntimeError(
                    "No usable kanji vocabulary found in this file."
                )

            with self._lock:
                self._state["phase"] = "writing"

            con = db.connect()
            try:
                ingest.write_deck(
                    con, self.deck_name, result,
                    description=f"Vocabulary from {self.display_name}",
                    source=str(self.path) if self.path else "(pasted text)",
                    created_at=date.today().isoformat(),
                )
            finally:
                con.close()

            with self._lock:
                self._state["phase"] = "done"
                self._state["result"] = result
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._state["phase"] = "error"
                self._state["error"] = str(exc)

    # ------------------------------------------------------------------ #
    def update(self, dt: float) -> None:
        with self._lock:
            phase = self._state["phase"]
            done = self._state["done"]
            total = self._state["total"]
            word = self._state["word"]
            result = self._state["result"]
            error = self._state["error"]

        if phase == "reading":
            self.phase_label.text = tr("IMPORT_READING")
            self.detail_label.text = ""
            self.count_label.text = ""
            self._set_bar(0)
        elif phase == "tokenising":
            self.phase_label.text = tr("IMPORT_TOKEN")
            self.detail_label.text = tr("IMPORT_TOKEN_HINT")
            self.count_label.text = ""
            self._set_bar(0.04)
        elif phase == "resolving":
            self.phase_label.text = tr("IMPORT_LOOKUP")
            self.detail_label.text = word
            frac = (done / total) if total else 0
            self.count_label.text = tr("IMPORT_LOOKUP_HINT", done=done, total=total)
            self._set_bar(0.05 + 0.85 * frac)
        elif phase == "writing":
            self.phase_label.text = tr("IMPORT_WRITE")
            self.detail_label.text = ""
            self._set_bar(0.95)
        elif phase == "done":
            self.phase_label.text = tr("IMPORT_DONE")
            self.phase_label.color = theme.with_alpha(theme.SUCCESS, 255)
            assert result is not None
            self.detail_label.text = tr(
                "IMPORT_DONE_DETAIL",
                words=len(result.words), kanji=len(result.kanji),
            )
            self.count_label.text = tr("IMPORT_RETURN")
            self._set_bar(1.0)
            self._done_elapsed += dt
            if self._done_elapsed > 1.2 and not self._dismissed:
                self._dismissed = True
                self.app.go_menu()
        elif phase == "error":
            self.phase_label.text = tr("IMPORT_FAIL")
            self.phase_label.color = theme.with_alpha(theme.DANGER, 255)
            self.detail_label.text = error or "unknown error"
            self.count_label.text = tr("IMPORT_BACK_HINT")
            self._set_bar(0)

    def _set_bar(self, frac: float) -> None:
        frac = max(0.0, min(1.0, frac))
        self.bar_fg.width = max(0, int(self.bar_bg.width * frac))

    # ------------------------------------------------------------------ #
    def on_mouse_press(self, x, y, button, modifiers) -> None:
        for b in self.buttons:
            if b.contains(x, y):
                b.click()
                break

    def on_mouse_motion(self, x, y, dx, dy) -> None:
        for b in self.buttons:
            b.set_hover(b.contains(x, y))

    def on_key_press(self, symbol, modifiers) -> None:
        from pyglet.window import key

        if symbol == key.ESCAPE and self._state["phase"] in ("done", "error"):
            self.app.go_menu()

    # ------------------------------------------------------------------ #
    def on_resize(self, width, height) -> None:
        cx, cy = width / 2, height / 2
        self.title.x, self.title.y = cx, cy + 110
        self.subtitle.x, self.subtitle.y = cx, cy + 78
        self.phase_label.x, self.phase_label.y = cx, cy + 28
        self.detail_label.x, self.detail_label.y = cx, cy + 6
        self.count_label.x, self.count_label.y = cx, cy - 50

        bar_w = min(520, width - 120)
        self.bar_bg.x = cx - bar_w / 2
        self.bar_bg.y = cy - 22
        self.bar_bg.width = bar_w
        self.bar_bg.height = 10
        self.bar_fg.x = self.bar_bg.x
        self.bar_fg.y = self.bar_bg.y
        self.bar_fg.height = 10

        self.back_btn.set_rect(cx - 95, cy - 110, 190, 38)

    def draw(self) -> None:
        # Flat background painted by window.clear() (glClearColor).
        self.batch.draw()

    def on_exit(self) -> None:
        for b in self.buttons:
            b.delete()
