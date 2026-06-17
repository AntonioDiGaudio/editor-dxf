"""GUI-based DXF editor using tkinter and ezdxf."""

from __future__ import annotations

import math
import os
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog
from typing import Any, Callable

import ezdxf
from ezdxf.math import Vec2

from app.constants import (
    SELECT_COLOR, HIGHLIGHT_COLOR, GRID_COLOR, BG_COLOR,
    ENTITY_COLOR, DIM_COLOR, HATCH_COLOR,
    MAX_UNDO, PICK_TOLERANCE, HANDLE_SIZE,
    NON_GRAPHICAL_TYPES,
)
from app.viewport import Viewport, entity_bbox
from app.geometry import (
    point_line_distance, segment_in_rect,
    is_polygon_closed, safe_dxf, get_location, move_entity, clean_mtext,
)
from app.entities import serialize_entity, deserialize_entity
from app.colors import aci_to_hex, tkinter_arc_direction
from app.oda import ODAConverter
from app.colors import aci_to_hex, tkinter_arc_direction


# ── Color Utilities ─────────────────────────────────────────────────

def _rgb_to_aci(r: int, g: int, b: int) -> int:
    """Find closest ACI color index from RGB."""
    from app.colors import _ACI_COLORS
    best = 7
    best_dist = float("inf")
    for idx, hex_c in _ACI_COLORS.items():
        if isinstance(idx, int) and 1 <= idx <= 255:
            cr = int(hex_c[1:3], 16)
            cg = int(hex_c[3:5], 16)
            cb = int(hex_c[5:7], 16)
            d = (r - cr) ** 2 + (g - cg) ** 2 + (b - cb) ** 2
            if d < best_dist:
                best_dist = d
                best = idx
                if d == 0:
                    break
    return best


# ── Color Picker Dialog ──────────────────────────────────────────────

class ColorPicker(tk.Toplevel):
    """Dialog to pick a color from a palette or enter hex code."""

    SWATCHES = [
        "#FF0000", "#FF4400", "#FF8800", "#FFCC00", "#FFFF00",
        "#AAFF00", "#44FF00", "#00FF00", "#00FF88", "#00FFCC",
        "#00FFFF", "#00AAFF", "#0044FF", "#0000FF", "#4400FF",
        "#8800FF", "#CC00FF", "#FF00FF", "#FF00AA", "#FF0044",
        "#FFFFFF", "#CCCCCC", "#999999", "#666666", "#333333",
        "#000000",
    ]

    def __init__(self, parent: tk.Widget, initial: str = "#FFFFFF"):
        super().__init__(parent)
        self.title("Scegli colore")
        self.result: str | None = None
        self.transient(parent)
        self.grab_set()
        self.configure(bg="#1A1A1A")

        frame = tk.Frame(self, bg="#1A1A1A", padx=12, pady=12)
        frame.pack()

        tk.Label(frame, text="Colori:", bg="#1A1A1A", fg="#CCCCCC",
                 font=("Segoe UI", 9, "bold")).pack(anchor=tk.W)

        swatch_frame = tk.Frame(frame, bg="#1A1A1A")
        swatch_frame.pack(pady=6)
        for i, color in enumerate(self.SWATCHES):
            btn = tk.Canvas(swatch_frame, width=22, height=22, bg=color,
                            highlightthickness=1, highlightbackground="#444444",
                            cursor="hand2")
            btn.grid(row=i // 5, column=i % 5, padx=2, pady=2)
            btn.bind("<Button-1>", lambda e, c=color: self._pick(c))

        tk.Label(frame, text="Codice esadecimale:", bg="#1A1A1A", fg="#CCCCCC",
                 font=("Segoe UI", 9, "bold")).pack(anchor=tk.W, pady=(8, 2))

        hex_frame = tk.Frame(frame, bg="#1A1A1A")
        hex_frame.pack(fill=tk.X)

        self.hex_var = tk.StringVar(value=initial)
        self.hex_entry = tk.Entry(hex_frame, textvariable=self.hex_var,
                                   bg="#2A2A2A", fg="#FFFFFF",
                                   insertbackground="#FFFFFF",
                                   font=("Consolas", 10), width=9)
        self.hex_entry.pack(side=tk.LEFT, padx=(0, 4))
        self.hex_entry.bind("<KeyRelease>", self._on_hex_key)

        # Preview
        self.preview = tk.Canvas(hex_frame, width=30, height=22,
                                  bg=initial, highlightthickness=0)
        self.preview.pack(side=tk.LEFT)

        btn_frame = tk.Frame(frame, bg="#1A1A1A")
        btn_frame.pack(fill=tk.X, pady=(10, 0))

        tk.Button(btn_frame, text="OK", command=self._ok,
                  bg="#2A7A2A", fg="white", relief=tk.FLAT,
                  padx=16, pady=2, cursor="hand2").pack(side=tk.RIGHT, padx=(4, 0))
        tk.Button(btn_frame, text="Annulla", command=self.destroy,
                  bg="#4A4A4A", fg="white", relief=tk.FLAT,
                  padx=16, pady=2, cursor="hand2").pack(side=tk.RIGHT)

        self.geometry(f"+{parent.winfo_rootx() + 50}+{parent.winfo_rooty() + 50}")

    def _pick(self, color: str) -> None:
        self.hex_var.set(color)
        self.preview.configure(bg=color)
        self.result = color
        self.destroy()

    def _on_hex_key(self, event: tk.Event) -> None:
        val = self.hex_var.get().strip()
        if len(val) == 7 and val[0] == "#":
            try:
                int(val[1:], 16)
                self.preview.configure(bg=val)
            except Exception:
                pass

    def _ok(self) -> None:
        val = self.hex_var.get().strip()
        if len(val) == 7 and val[0] == "#":
            try:
                int(val[1:], 16)
                self.result = val
            except Exception:
                pass
        self.destroy()


# ── Main Editor ──────────────────────────────────────────────────────

class DXFEditor:
    """GUI-based DXF editor using tkinter and ezdxf."""

    def __init__(self, master: tk.Tk, filepath: str | None = None):
        self.master = master
        master.title("DXF Editor - [nessun file]")
        master.geometry("1200x800")

        self.doc: Any = None
        self.msp: Any = None
        self.filepath: str | None = None
        self.modified = False

        # Viewport
        self.view = Viewport()
        self.font_height = 12

        # Selection
        self.selected_entities: list[Any] = []
        self.selection_handles: set[str] = set()

        # Undo
        self.undo_stack: list[list[dict]] = []
        self.redo_stack: list[list[dict]] = []
        self._suppress_undo = False

        # Clipboard
        self.clipboard: list[dict] = []

        # Interaction state
        self.drag_start: tuple[float, float] | None = None
        self.drag_rect: tuple[float, float, float, float] | None = None
        self.moving = False
        self.move_start_world: tuple[float, float] | None = None
        self.panning = False
        self.pan_start: tuple[float, float] | None = None
        self.rect_id: int | None = None
        self.last_motion: tuple[int, int] | None = None

        # ODA
        self.oda = ODAConverter()

        # Entity filter toggle
        self.filter_enabled = False
        self._render_errors: set[str] = set()

        # Drawing state
        self.draw_line_mode = False
        self.draw_text_mode = False
        self.line_start: tuple[float, float] | None = None

        # Resize state
        self.resizing = False
        self.resizing_entity: Any = None
        self.resize_start_world: tuple[float, float] | None = None
        self.resize_initial_height: float = 10.0

        # Save counter for incremental saves
        self.save_counter = 0

        # ── Top Toolbar ────────────────────────────────────────────────
        toolbar = tk.Frame(master, bg="#1A1A1A", height=34)
        toolbar.pack(side=tk.TOP, fill=tk.X)
        toolbar.pack_propagate(False)

        btn_style = {
            "bg": "#2A2A2A", "fg": "#CCCCCC", "activebackground": "#444444",
            "activeforeground": "#FFFFFF", "relief": tk.FLAT,
            "padx": 6, "pady": 2, "font": ("Segoe UI", 8, "bold"),
            "cursor": "hand2", "bd": 0, "highlightthickness": 0
        }

        def tbtn(text: str, cmd: Callable, tooltip: str = "") -> tk.Button:
            b = tk.Button(toolbar, text=text, command=cmd, **btn_style)
            b.pack(side=tk.LEFT, padx=1, pady=2)
            if tooltip:
                self._add_tooltip(b, tooltip)
            return b

        def sep() -> None:
            f = tk.Frame(toolbar, width=1, bg="#444444", height=20)
            f.pack(side=tk.LEFT, padx=3, pady=4)

        # File
        tbtn("Apri", self._prompt_open, "Apri DXF (Ctrl+O)")
        tbtn("Salva", self._save, "Salva DXF (Ctrl+S)")
        sep()

        # Undo/Redo
        tbtn("Annulla", lambda: self._on_undo(None), "Annulla (Ctrl+Z)")
        tbtn("Ripeti", lambda: self._on_redo(None), "Ripeti (Ctrl+Y)")
        sep()

        # Clipboard
        tbtn("Copia", lambda: self._on_copy(None), "Copia (Ctrl+C)")
        tbtn("Incolla", lambda: self._on_paste(None), "Incolla (Ctrl+V)")
        tbtn("Taglia", lambda: self._on_cut(None), "Taglia (Ctrl+X)")
        tbtn("Elimina", self._delete_selected, "Elimina (Canc)")
        sep()

        # Select
        tbtn("Sel.tutto", self._select_all, "Seleziona tutto (Ctrl+A)")
        tbtn("Deselez.", self._clear_selection, "Deseleziona tutto")
        sep()

        # Align
        tbtn("Allinea", self._align_all_to_origin, "Allinea tutto il disegno all'origine (0,0)")
        sep()

        # Filter toggle
        tbtn("Filtro", self._toggle_filter, "Mostra/nascondi entità interne DXF")
        sep()

        # Draw tools
        tbtn("Linea", self._toggle_line_mode, "Disegna linea (click due punti)")
        tbtn("Testo", self._toggle_text_mode, "Inserisci testo (click punto)")
        sep()

        # Color
        tbtn("Colore", self._change_color, "Cambia colore entità selezionate")
        sep()

        # DWG → DXF
        tbtn("DWG→DXF", self._convert_dwg_to_dxf, "Carica DWG e converti in DXF")
        sep()

        # Info
        tbtn("Info", self._show_info, "Informazioni file")

        # ── Info Bar (controls + coords) ───────────────────────────────
        infobar = tk.Frame(master, bg="#0D0D0D", height=28)
        infobar.pack(side=tk.TOP, fill=tk.X)
        infobar.pack_propagate(False)

        ctrl_text = (
            "  Mouse: Sinistro=Seleziona/sposta  |  Destro=Pan  |  Rotella=Zoom  |  "
            "Trackpad: Scorri=Pan  |  Pizzica=Zoom"
        )
        self.ctrl_label = tk.Label(
            infobar, text=ctrl_text, anchor=tk.W, padx=8,
            bg="#0D0D0D", fg="#888888", font=("Segoe UI", 9)
        )
        self.ctrl_label.pack(side=tk.LEFT)

        self.coord_label = tk.Label(
            infobar, text="(0.00, 0.00)", anchor=tk.E, padx=8,
            bg="#0D0D0D", fg="#888888", font=("Segoe UI", 7)
        )
        self.coord_label.pack(side=tk.RIGHT)

        # ── Text Entity Panel ──────────────────────────────────────────
        text_panel = tk.Frame(master, bg="#1A1A1A", height=80)
        text_panel.pack(side=tk.TOP, fill=tk.X)
        text_panel.pack_propagate(False)

        tk.Label(text_panel, text="Testi:", bg="#1A1A1A", fg="#CCCCCC",
                 font=("Segoe UI", 8, "bold")).pack(side=tk.LEFT, padx=(4, 2))

        list_frame = tk.Frame(text_panel, bg="#2A2A2A")
        list_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 4), pady=2)

        text_scrollbar = tk.Scrollbar(list_frame, orient=tk.VERTICAL)
        self.text_listbox = tk.Listbox(
            list_frame, bg="#2A2A2A", fg="#CCCCCC",
            selectbackground="#4A7A4A", selectforeground="#FFFFFF",
            font=("Consolas", 8), height=4, bd=0, highlightthickness=0,
            yscrollcommand=text_scrollbar.set, exportselection=False
        )
        text_scrollbar.config(command=self.text_listbox.yview)
        text_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.text_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.text_listbox.bind("<<ListboxSelect>>", self._on_text_list_select)

        self._text_entity_list: list[Any] = []

        # ── Tab Bar ─────────────────────────────────────────────────────
        self.tab_bar = tk.Frame(master, bg="#0D0D0D", height=26)
        self.tab_bar.pack(side=tk.TOP, fill=tk.X)
        self.tab_bar.pack_propagate(False)

        self._tabs: list[dict] = []
        self._current_tab: int = -1

        # ── Canvas ────────────────────────────────────────────────────
        self.canvas = tk.Canvas(master, bg=BG_COLOR, highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        # ── Status bar ────────────────────────────────────────────────
        self.status = tk.Label(
            master, text="Pronto", anchor=tk.W, padx=8,
            bg="#0D0D0D", fg="#666666", font=("Segoe UI", 8)
        )
        self.status.pack(fill=tk.X)

        # ── Menu ──────────────────────────────────────────────────────
        self._setup_menu()

        # ── Bind events ───────────────────────────────────────────────
        self.canvas.bind("<ButtonPress-1>", self._on_lbutton_down)
        self.canvas.bind("<B1-Motion>", self._on_lbutton_move)
        self.canvas.bind("<ButtonRelease-1>", self._on_lbutton_up)
        self.canvas.bind("<ButtonPress-2>", self._on_mbutton_down)
        self.canvas.bind("<B2-Motion>", self._on_mbutton_move)
        self.canvas.bind("<ButtonPress-3>", self._on_rbutton_down)
        self.canvas.bind("<B3-Motion>", self._on_rbutton_move)
        self.canvas.bind("<ButtonRelease-3>", self._on_rbutton_up)
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Configure>", self._on_configure)
        master.bind("<Control-z>", self._on_undo)
        master.bind("<Control-y>", self._on_redo)
        master.bind("<Control-c>", self._on_copy)
        master.bind("<Control-v>", self._on_paste)
        master.bind("<Delete>", self._on_delete)
        master.bind("<BackSpace>", self._on_delete)
        master.bind("<Control-a>", self._on_select_all)
        master.bind("<Control-o>", lambda e: self._prompt_open())
        master.bind("<Control-s>", lambda e: self._save())
        master.bind("<Key-Escape>", self._on_escape)

        # Init first tab
        self._new_tab()
        if filepath:
            self.master.after(100, lambda: self._load_file(filepath))
        else:
            self._rebuild_tab_bar()

    # ── Tooltip helper ─────────────────────────────────────────────────

    _tip_window: tk.Toplevel | None = None

    def _add_tooltip(self, widget: tk.Widget, text: str) -> None:
        def show(event: tk.Event) -> None:
            if self._tip_window:
                return
            x = event.x_root + 12
            y = event.y_root + 8
            self._tip_window = tw = tk.Toplevel(widget)
            tw.wm_overrideredirect(True)
            tw.wm_geometry(f"+{x}+{y}")
            tw.attributes("-topmost", True)
            lbl = tk.Label(tw, text=text, bg="#222222", fg="#CCCCCC",
                           font=("Segoe UI", 8), padx=6, pady=2,
                           relief=tk.SOLID, bd=1)
            lbl.pack()

        def hide(event: tk.Event) -> None:
            if self._tip_window:
                self._tip_window.destroy()
                self._tip_window = None

        widget.bind("<Enter>", show)
        widget.bind("<Leave>", hide)

    # ── Menu ──────────────────────────────────────────────────────────

    def _setup_menu(self) -> None:
        menubar = tk.Menu(self.master)
        self.master.config(menu=menubar)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Apri DXF...", command=self._prompt_open,
                              accelerator="Ctrl+O")
        file_menu.add_command(label="Salva", command=self._save,
                              accelerator="Ctrl+S")
        file_menu.add_command(label="Salva come...", command=self._save_as)
        file_menu.add_command(label="Salva come DWG...", command=self._save_as_dwg)
        file_menu.add_separator()
        file_menu.add_command(label="Carica DWG (ODA Converter)...",
                              command=self._convert_dwg_to_dxf)
        file_menu.add_separator()
        file_menu.add_command(label="Esci", command=self.master.quit)
        menubar.add_cascade(label="File", menu=file_menu)

        edit_menu = tk.Menu(menubar, tearoff=0)
        edit_menu.add_command(label="Annulla", command=self._on_undo,
                              accelerator="Ctrl+Z")
        edit_menu.add_command(label="Ripeti", command=self._on_redo,
                              accelerator="Ctrl+Y")
        edit_menu.add_separator()
        edit_menu.add_command(label="Taglia", command=self._on_cut,
                              accelerator="Ctrl+X")
        edit_menu.add_command(label="Copia", command=self._on_copy,
                              accelerator="Ctrl+C")
        edit_menu.add_command(label="Incolla", command=self._on_paste,
                              accelerator="Ctrl+V")
        edit_menu.add_separator()
        edit_menu.add_command(label="Elimina", command=self._delete_selected,
                              accelerator="Canc")
        edit_menu.add_command(label="Seleziona tutto", command=self._select_all,
                              accelerator="Ctrl+A")
        menubar.add_cascade(label="Modifica", menu=edit_menu)

        tools_menu = tk.Menu(menubar, tearoff=0)
        tools_menu.add_command(label="Allinea selezione a origine",
                               command=self._align_to_origin)
        tools_menu.add_command(label="Adatta alla finestra",
                               command=self._fit_view)
        tools_menu.add_command(label="Deseleziona tutto",
                               command=self._clear_selection)
        tools_menu.add_separator()
        tools_menu.add_command(label="Configura ODA Converter...",
                               command=self._configure_oda)
        tools_menu.add_separator()
        tools_menu.add_command(label="Informazioni file...",
                               command=self._show_info)
        menubar.add_cascade(label="Strumenti", menu=tools_menu)

    def _configure_oda(self) -> None:
        path = self.oda.prompt_path()
        if path:
            self._status(f"ODA Converter configurato: {path}")
        else:
            self._status("ODA Converter non configurato")
        self._render()

    # ── Entity Filter ─────────────────────────────────────────────────

    def _toggle_filter(self) -> None:
        self.filter_enabled = not self.filter_enabled
        self._render()
        self._status(f"Filtro entità: {'ON' if self.filter_enabled else 'OFF'}")

    def _is_entity_visible(self, entity: Any) -> bool:
        if not self.filter_enabled:
            return True
        et = entity.dxftype()
        if et in NON_GRAPHICAL_TYPES:
            return False
        try:
            if safe_dxf(entity, "invisible", 0):
                return False
        except Exception:
            pass
        try:
            if getattr(entity, 'is_alive', True) is False:
                return False
        except Exception:
            pass
        try:
            loc = get_location(entity)
            if math.isnan(loc.x) or math.isnan(loc.y) or math.isinf(loc.x) or math.isinf(loc.y):
                return False
        except Exception:
            pass
        try:
            layer_name = safe_dxf(entity, "layer", "0")
            if layer_name and self.doc and self.doc.layers.has_entry(layer_name):
                layer = self.doc.layers.get(layer_name)
                flags = safe_dxf(layer, "flags", 0) or 0
                is_off = safe_dxf(layer, "off", False)
                if flags & 1 or flags & 2 or is_off:
                    return False
        except Exception:
            pass
        return True

    def _get_visible_entities(self) -> list[Any]:
        if self.msp is None:
            return []
        entities = list(self.msp)
        if self.doc is not None:
            for layout in self.doc.layouts:
                if layout.name != "Model":
                    entities.extend(list(layout))
        return [e for e in entities if self._is_entity_visible(e)]

    # ── Draw Tools ────────────────────────────────────────────────────

    def _toggle_line_mode(self) -> None:
        self.draw_line_mode = not self.draw_line_mode
        self.draw_text_mode = False
        self.line_start = None
        self.canvas.delete("preview")
        msg = "Linea: click primo punto" if self.draw_line_mode else "Pronto"
        self._status(msg)
        if self.draw_line_mode:
            self.canvas.config(cursor="crosshair")
        else:
            self.canvas.config(cursor="")

    def _toggle_text_mode(self) -> None:
        self.draw_text_mode = not self.draw_text_mode
        self.draw_line_mode = False
        self.line_start = None
        msg = "Testo: click per posizionare" if self.draw_text_mode else "Pronto"
        self._status(msg)
        if self.draw_text_mode:
            self.canvas.config(cursor="xterm")
        else:
            self.canvas.config(cursor="")

    def _add_line(self, x1: float, y1: float, x2: float, y2: float) -> None:
        if self.doc is None:
            return
        self._save_undo()
        self.msp.add_line((x1, y1), (x2, y2))
        self.msp = self.doc.modelspace()
        self.modified = True
        self._render()
        self._status(f"Linea da ({x1:.2f},{y1:.2f}) a ({x2:.2f},{y2:.2f})")

    def _add_text(self, x: float, y: float, text: str) -> None:
        if self.doc is None or not text:
            return
        self._save_undo()
        self.msp.add_text(text, height=10, dxfattribs={"insert": (x, y)})
        self.msp = self.doc.modelspace()
        self.modified = True
        self._render()
        self._status(f"Testo inserito: '{text[:30]}'")

    # ── Change Color ──────────────────────────────────────────────────

    def _change_color(self) -> None:
        if not self.selected_entities:
            self._status("Nessuna entità selezionata")
            return
        if self.doc is None:
            return

        # Show color picker
        picker = ColorPicker(self.master, initial="#FFFFFF")
        self.master.wait_window(picker)
        chosen = picker.result
        if not chosen:
            return

        # Convert hex to ACI (find closest)
        r, g, b = int(chosen[1:3], 16), int(chosen[3:5], 16), int(chosen[5:7], 16)
        aci = _rgb_to_aci(r, g, b)

        self._save_undo()
        for entity in self.selected_entities:
            try:
                entity.dxf.color = aci
            except Exception:
                pass
        self.modified = True
        self._render()
        self._status(f"Colore cambiato in {chosen} (ACI {aci})")

    # ── DWG → DXF Conversion ──────────────────────────────────────────

    def _convert_dwg_to_dxf(self) -> None:
        dwg_path = filedialog.askopenfilename(
            title="Seleziona file DWG",
            filetypes=[("DWG files", "*.dwg"), ("Tutti i file", "*.*")]
        )
        if not dwg_path:
            return

        suggested = os.path.splitext(dwg_path)[0] + ".dxf"
        out_dxf = filedialog.asksaveasfilename(
            title="Salva DXF come...",
            initialfile=os.path.basename(suggested),
            defaultextension=".dxf",
            filetypes=[("DXF files", "*.dxf"), ("Tutti i file", "*.*")]
        )
        if not out_dxf:
            return

        def status(msg: str) -> None:
            self._status(msg)
            self.master.update()

        ok = self.oda.convert(dwg_path, out_dxf, status_callback=status)
        if ok and os.path.isfile(out_dxf):
            self._load_file(out_dxf)
            self._status(f"Convertito e caricato: {os.path.basename(out_dxf)}")

    # ── File I/O ──────────────────────────────────────────────────────

    def _prompt_open(self) -> None:
        path = filedialog.askopenfilename(
            title="Apri file DXF",
            filetypes=[("DXF files", "*.dxf"), ("Tutti i file", "*.*")]
        )
        if path:
            self._load_file(path)

    def _load_file(self, path: str) -> None:
        if not os.path.exists(path):
            messagebox.showerror("Errore", f"File non trovato:\n{path}")
            return
        try:
            doc = ezdxf.readfile(path)
        except Exception as e:
            messagebox.showerror("Errore", f"Impossibile leggere il DXF:\n{e}")
            return

        current_is_empty = (self._current_tab >= 0
                            and self._tabs[self._current_tab].get('doc') is None
                            and not self._tabs[self._current_tab].get('filepath'))
        if not current_is_empty:
            self._new_tab()

        self.doc = doc
        self.msp = doc.modelspace()
        self.filepath = path
        self.modified = False
        self.selected_entities = []
        self.selection_handles = set()
        self.undo_stack = []
        self.redo_stack = []
        self._render_errors.clear()
        self.save_counter = 0
        self._save_undo()
        self._fit_view()
        self._rebuild_tab_bar()
        self._update_title()
        self._render()
        self._status(f"Caricato: {os.path.basename(path)}")

    def _save(self) -> None:
        if self.doc is None:
            return
        if not self.filepath:
            self._save_as()
            return
        self.save_counter += 1
        base, _ = os.path.splitext(self.filepath)
        path = f"{base}_{self.save_counter}.dxf"
        try:
            self.doc.saveas(path)
            self.filepath = path
            self.modified = False
            self._rebuild_tab_bar()
            self._update_title()
            self._status(f"Salvato: {os.path.basename(path)}")
        except Exception as e:
            self.save_counter -= 1
            messagebox.showerror("Errore", f"Salvataggio fallito:\n{e}")

    def _save_as(self) -> None:
        if self.doc is None:
            return
        path = filedialog.asksaveasfilename(
            title="Salva come DXF",
            defaultextension=".dxf",
            filetypes=[("DXF files", "*.dxf"), ("Tutti i file", "*.*")]
        )
        if not path:
            return
        try:
            self.doc.saveas(path)
            self.filepath = path
            self.modified = False
            self._rebuild_tab_bar()
            self._update_title()
            self._status(f"Salvato: {os.path.basename(path)}")
        except Exception as e:
            self.save_counter -= 1
            messagebox.showerror("Errore", f"Salvataggio fallito:\n{e}")

    def _save_as_dwg(self) -> None:
        if self.doc is None:
            return
        path = filedialog.asksaveasfilename(
            title="Salva come DWG",
            defaultextension=".dwg",
            filetypes=[("DWG files", "*.dwg"), ("Tutti i file", "*.*")]
        )
        if not path:
            return
        try:
            self.doc.saveas(path)
            self.filepath = path
            self.modified = False
            self._rebuild_tab_bar()
            self._update_title()
            self._status(f"Salvato: {os.path.basename(path)}")
        except Exception as e:
            messagebox.showerror("Errore", f"Salvataggio DWG fallito:\n{e}")

    def _show_info(self) -> None:
        if self.doc is None:
            messagebox.showinfo("Info", "Nessun file caricato.")
            return
        info = [
            f"File: {self.filepath or '(nuovo)'}",
            f"Versione: {self.doc.header.get('$ACADVER', 'N/A')}",
            f"Entità Model Space: {len(list(self.msp)) if self.msp else 0}",
            f"Selezionate: {len(self.selected_entities)}",
            f"Undo: {len(self.undo_stack)}",
            f"Redo: {len(self.redo_stack)}",
            f"Scala vista: {self.view.scale:.6f}",
            f"Centro vista: ({self.view.view_x:.2f}, {self.view.view_y:.2f})",
            "",
            f"ODA Converter: {self.oda.path or 'Non configurato'}",
        ]
        messagebox.showinfo("Informazioni file", "\n".join(info))

    # ── Undo/Redo ─────────────────────────────────────────────────────

    def _save_undo(self) -> None:
        if self._suppress_undo or self.doc is None:
            return
        snapshot = self._snapshot()
        self.undo_stack.append(snapshot)
        if len(self.undo_stack) > MAX_UNDO:
            self.undo_stack.pop(0)
        self.redo_stack.clear()

    def _snapshot(self) -> list[dict]:
        if self.msp is None:
            return []
        return [serialize_entity(e) for e in self.msp]

    def _restore_snapshot(self, snapshot: list[dict]) -> None:
        if self.doc is None:
            return
        self._suppress_undo = True
        self.msp = self.doc.modelspace()
        for e in list(self.msp):
            try:
                self.msp.delete_entity(e)
            except Exception:
                pass
        for data in snapshot:
            try:
                deserialize_entity(self.doc, data, self.msp)
            except Exception as exc:
                print(f"Restore warning: {exc}")
        self.doc.modelspace()
        self._suppress_undo = False

    def _on_undo(self, event: Any = None) -> None:
        if not self.undo_stack or self.doc is None:
            return
        current = self._snapshot()
        self.redo_stack.append(current)
        snapshot = self.undo_stack.pop()
        self.selected_entities = []
        self.selection_handles = set()
        self._restore_snapshot(snapshot)
        self.msp = self.doc.modelspace()
        self.modified = True
        self._render()
        self._status("Annullato")

    def _on_redo(self, event: Any = None) -> None:
        if not self.redo_stack or self.doc is None:
            return
        current = self._snapshot()
        self.undo_stack.append(current)
        snapshot = self.redo_stack.pop()
        self.selected_entities = []
        self.selection_handles = set()
        self._restore_snapshot(snapshot)
        self.msp = self.doc.modelspace()
        self.modified = True
        self._render()
        self._status("Ripetuto")

    # ── Clipboard ─────────────────────────────────────────────────────

    def _on_copy(self, event: Any = None) -> None:
        if not self.selected_entities:
            self._status("Niente da copiare")
            return
        self.clipboard = [serialize_entity(e) for e in self.selected_entities]
        self._status(f"Copiate {len(self.clipboard)} entità")

    def _on_cut(self, event: Any = None) -> None:
        if not self.selected_entities:
            return
        self._on_copy(event)
        self._delete_selected()

    def _on_paste(self, event: Any = None) -> None:
        if not self.clipboard or self.doc is None:
            self._status("Appunti vuoti")
            return
        self._save_undo()
        self.selected_entities = []
        self.selection_handles = set()
        for data in self.clipboard:
            try:
                entity = deserialize_entity(self.doc, data, self.msp)
                if entity is not None:
                    self.selected_entities.append(entity)
                    h = safe_dxf(entity, "handle")
                    if h:
                        self.selection_handles.add(str(h))
            except Exception as e:
                print(f"Paste warning: {e}")
        self.msp = self.doc.modelspace()
        self.modified = True
        self._render()
        self._status(f"Incollate {len(self.clipboard)} entità")

    # ── Selection ─────────────────────────────────────────────────────

    def _get_all_entities(self) -> list[Any]:
        return self._get_visible_entities()

    def _on_escape(self, event: Any = None) -> None:
        self.selected_entities = []
        self.selection_handles = set()
        self.draw_line_mode = False
        self.draw_text_mode = False
        self.line_start = None
        self.moving = False
        self.move_start_world = None
        self.drag_start = None
        self.drag_rect = None
        self.resizing = False
        self.resizing_entity = None
        self.resize_start_world = None
        self.canvas.config(cursor="")
        self.canvas.delete("preview")
        self._render()
        self._status("Pronto")

    def _clear_selection(self) -> None:
        self.selected_entities = []
        self.selection_handles = set()
        self._render()
        self._status("Selezione cancellata")

    def _select_all(self) -> None:
        self._on_select_all(None)

    def _on_select_all(self, event: Any = None) -> None:
        visible = self._get_visible_entities()
        self.selected_entities = list(visible)
        self.selection_handles = {
            str(safe_dxf(e, "handle", ""))
            for e in self.selected_entities
        }
        self._render()
        self._status(f"Selezionate tutte ({len(self.selected_entities)})")

    def _hit_test(self, wx: float, wy: float) -> Any | None:
        visible = self._get_visible_entities()
        best_dist = float("inf")
        best_entity = None
        for entity in visible:
            et = entity.dxftype()
            try:
                if et in ("LINE", "XLINE", "RAY"):
                    s = safe_dxf(entity, "start")
                    e = safe_dxf(entity, "end")
                    if s is not None and e is not None:
                        dist = point_line_distance(
                            Vec2(wx, wy),
                            Vec2(s[0], s[1]),
                            Vec2(e[0], e[1]),
                        )
                        if dist < best_dist:
                            best_dist = dist
                            best_entity = entity
                elif et == "CIRCLE":
                    c = safe_dxf(entity, "center")
                    r = safe_dxf(entity, "radius")
                    if c is not None and r is not None:
                        dist = abs(Vec2(wx - c[0], wy - c[1]).magnitude - r)
                        if dist < best_dist:
                            best_dist = dist
                            best_entity = entity
                elif et == "ARC":
                    c = safe_dxf(entity, "center")
                    r = safe_dxf(entity, "radius")
                    if c is not None and r is not None:
                        dist = abs(Vec2(wx - c[0], wy - c[1]).magnitude - r)
                        if dist < best_dist:
                            best_dist = dist
                            best_entity = entity
                elif et in ("TEXT", "MTEXT"):
                    bbox = entity_bbox(entity)
                    if bbox is not None:
                        x1, y1 = bbox[0].x, bbox[0].y
                        x2, y2 = bbox[1].x, bbox[1].y
                        if x1 <= wx <= x2 and y1 <= wy <= y2:
                            d = 0.0
                        else:
                            dx = 0.0 if x1 <= wx <= x2 else min(abs(wx - x1), abs(wx - x2))
                            dy = 0.0 if y1 <= wy <= y2 else min(abs(wy - y1), abs(wy - y2))
                            d = math.sqrt(dx * dx + dy * dy)
                        if d < best_dist:
                            best_dist = d
                            best_entity = entity
                elif et == "LWPOLYLINE":
                    pts = list(entity.get_points(format='xy'))
                    best = float("inf")
                    for i in range(len(pts) - 1):
                        d = point_line_distance(
                            Vec2(wx, wy),
                            Vec2(pts[i][0], pts[i][1]),
                            Vec2(pts[i + 1][0], pts[i + 1][1]),
                        )
                        if d < best:
                            best = d
                    if len(pts) > 0:
                        last = Vec2(pts[-1][0], pts[-1][1])
                        d = Vec2(wx - last.x, wy - last.y).magnitude
                        if d < best:
                            best = d
                    if best < best_dist:
                        best_dist = best
                        best_entity = entity
                elif et == "POINT":
                    loc = safe_dxf(entity, "location")
                    if loc is not None:
                        d = Vec2(wx - loc[0], wy - loc[1]).magnitude
                        if d < best_dist:
                            best_dist = d
                            best_entity = entity
                elif et == "ELLIPSE":
                    c = safe_dxf(entity, "center")
                    if c is not None:
                        d = Vec2(wx - c[0], wy - c[1]).magnitude
                        if d < best_dist:
                            best_dist = d
                            best_entity = entity
                elif et == "INSERT":
                    ins = safe_dxf(entity, "insert")
                    if ins is not None:
                        d = Vec2(wx - ins[0], wy - ins[1]).magnitude
                        if d < best_dist:
                            best_dist = d
                            best_entity = entity
                else:
                    bbox = entity_bbox(entity)
                    if bbox is not None:
                        center = (bbox[0] + bbox[1]) / 2
                        d = Vec2(wx - center.x, wy - center.y).magnitude
                        if d < best_dist:
                            best_dist = d
                            best_entity = entity
            except Exception:
                continue
        tol = PICK_TOLERANCE / self.view.scale
        if best_dist < tol:
            return best_entity
        return None

    def _select_in_rect(
        self, wx1: float, wy1: float, wx2: float, wy2: float
    ) -> None:
        visible = self._get_visible_entities()
        xmin, xmax = min(wx1, wx2), max(wx1, wx2)
        ymin, ymax = min(wy1, wy2), max(wy1, wy2)
        self.selected_entities = []
        self.selection_handles = set()
        for entity in visible:
            et = entity.dxftype()
            try:
                if et in ("LINE", "XLINE", "RAY"):
                    s = safe_dxf(entity, "start")
                    e = safe_dxf(entity, "end")
                    if s is not None and e is not None:
                        if segment_in_rect(
                            Vec2(s[0], s[1]), Vec2(e[0], e[1]),
                            xmin, ymin, xmax, ymax
                        ):
                            self._add_to_selection(entity)
                elif et == "CIRCLE":
                    c = safe_dxf(entity, "center")
                    if c is not None and xmin <= c[0] <= xmax and ymin <= c[1] <= ymax:
                        self._add_to_selection(entity)
                elif et == "ARC":
                    c = safe_dxf(entity, "center")
                    if c is not None and xmin <= c[0] <= xmax and ymin <= c[1] <= ymax:
                        self._add_to_selection(entity)
                elif et in ("TEXT", "MTEXT"):
                    bbox = entity_bbox(entity)
                    if bbox is not None:
                        if (bbox[0].x <= xmax and bbox[1].x >= xmin and
                            bbox[0].y <= ymax and bbox[1].y >= ymin):
                            self._add_to_selection(entity)
                elif et == "LWPOLYLINE":
                    pts = list(entity.get_points(format='xy'))
                    for p in pts:
                        if xmin <= p[0] <= xmax and ymin <= p[1] <= ymax:
                            self._add_to_selection(entity)
                            break
                elif et == "POINT":
                    loc = safe_dxf(entity, "location")
                    if loc is not None and xmin <= loc[0] <= xmax and ymin <= loc[1] <= ymax:
                        self._add_to_selection(entity)
                elif et == "ELLIPSE":
                    c = safe_dxf(entity, "center")
                    if c is not None and xmin <= c[0] <= xmax and ymin <= c[1] <= ymax:
                        self._add_to_selection(entity)
                elif et == "INSERT":
                    ins = safe_dxf(entity, "insert")
                    if ins is not None and xmin <= ins[0] <= xmax and ymin <= ins[1] <= ymax:
                        self._add_to_selection(entity)
                elif et == "HATCH":
                    c = safe_dxf(entity, "center", None)
                    if c is not None and xmin <= c[0] <= xmax and ymin <= c[1] <= ymax:
                        self._add_to_selection(entity)
                elif et == "DIMENSION":
                    dp = safe_dxf(entity, "defpoint")
                    if dp is not None and xmin <= dp[0] <= xmax and ymin <= dp[1] <= ymax:
                        self._add_to_selection(entity)
                else:
                    bbox = entity_bbox(entity)
                    if bbox is not None:
                        cx = (bbox[0].x + bbox[1].x) / 2
                        cy = (bbox[0].y + bbox[1].y) / 2
                        if xmin <= cx <= xmax and ymin <= cy <= ymax:
                            self._add_to_selection(entity)
            except Exception:
                continue
        self._render()

    def _add_to_selection(self, entity: Any) -> None:
        self.selected_entities.append(entity)
        h = safe_dxf(entity, "handle", "")
        if h:
            self.selection_handles.add(str(h))

    # ── Delete ────────────────────────────────────────────────────────

    def _on_delete(self, event: Any = None) -> None:
        self._delete_selected()

    def _delete_selected(self) -> None:
        if not self.selected_entities or self.msp is None:
            self._status("Niente da eliminare")
            return
        self._save_undo()
        for entity in self.selected_entities:
            try:
                self.msp.delete_entity(entity)
            except Exception:
                pass
        self.selected_entities = []
        self.selection_handles = set()
        self.modified = True
        self._render()
        self._status("Entità eliminate")

    # ── Align to Origin ──────────────────────────────────────────────

    def _align_all_to_origin(self) -> None:
        if self.msp is None:
            return
        entities = self._get_visible_entities()
        if not entities:
            self._status("Nessuna entità da allineare")
            return
        self._save_undo()
        all_pts: list[Vec2] = []
        for entity in entities:
            et = entity.dxftype()
            try:
                if et == "LINE":
                    s = safe_dxf(entity, "start")
                    e = safe_dxf(entity, "end")
                    if s is not None: all_pts.append(Vec2(s[0], s[1]))
                    if e is not None: all_pts.append(Vec2(e[0], e[1]))
                elif et == "CIRCLE":
                    c = safe_dxf(entity, "center")
                    if c is not None: all_pts.append(Vec2(c[0], c[1]))
                elif et == "ARC":
                    c = safe_dxf(entity, "center")
                    if c is not None: all_pts.append(Vec2(c[0], c[1]))
                elif et in ("TEXT", "MTEXT"):
                    ins = safe_dxf(entity, "insert")
                    if ins is not None: all_pts.append(Vec2(ins[0], ins[1]))
                elif et == "LWPOLYLINE":
                    for p in entity.get_points(format='xy'):
                        all_pts.append(Vec2(p[0], p[1]))
                elif et == "POINT":
                    loc = safe_dxf(entity, "location")
                    if loc is not None: all_pts.append(Vec2(loc[0], loc[1]))
                elif et == "ELLIPSE":
                    c = safe_dxf(entity, "center")
                    if c is not None: all_pts.append(Vec2(c[0], c[1]))
                elif et == "INSERT":
                    ins = safe_dxf(entity, "insert")
                    if ins is not None: all_pts.append(Vec2(ins[0], ins[1]))
                elif et == "DIMENSION":
                    for attr in ("defpoint", "defpoint2", "defpoint3", "text_midpoint"):
                        v = safe_dxf(entity, attr, None)
                        if v is not None: all_pts.append(Vec2(v[0], v[1]))
                elif et in ("XLINE", "RAY"):
                    start = safe_dxf(entity, "start")
                    if start is not None: all_pts.append(Vec2(start[0], start[1]))
                else:
                    bbox = entity_bbox(entity)
                    if bbox is not None:
                        all_pts.append(bbox[0])
                        all_pts.append(bbox[1])
            except Exception:
                continue
        if not all_pts:
            return
        minx = min(p.x for p in all_pts)
        miny = min(p.y for p in all_pts)
        offset = Vec2(-minx, -miny)
        for entity in entities:
            move_entity(entity, offset)
        self.modified = True
        self._render()
        self._status(f"Disegno allineato all'origine (offset: {offset.x:.2f}, {offset.y:.2f})")

    def _align_to_origin(self) -> None:
        if not self.selected_entities or self.msp is None:
            self._status("Nessuna entità selezionata")
            return
        self._save_undo()
        all_pts: list[Vec2] = []
        for entity in self.selected_entities:
            et = entity.dxftype()
            try:
                if et == "LINE":
                    s = safe_dxf(entity, "start")
                    e = safe_dxf(entity, "end")
                    if s is not None:
                        all_pts.append(Vec2(s[0], s[1]))
                    if e is not None:
                        all_pts.append(Vec2(e[0], e[1]))
                elif et == "CIRCLE":
                    c = safe_dxf(entity, "center")
                    if c is not None:
                        all_pts.append(Vec2(c[0], c[1]))
                elif et == "ARC":
                    c = safe_dxf(entity, "center")
                    if c is not None:
                        all_pts.append(Vec2(c[0], c[1]))
                elif et in ("TEXT", "MTEXT"):
                    ins = safe_dxf(entity, "insert")
                    if ins is not None:
                        all_pts.append(Vec2(ins[0], ins[1]))
                elif et == "LWPOLYLINE":
                    for p in entity.get_points(format='xy'):
                        all_pts.append(Vec2(p[0], p[1]))
                elif et == "POINT":
                    loc = safe_dxf(entity, "location")
                    if loc is not None:
                        all_pts.append(Vec2(loc[0], loc[1]))
                elif et == "ELLIPSE":
                    c = safe_dxf(entity, "center")
                    if c is not None:
                        all_pts.append(Vec2(c[0], c[1]))
                elif et == "INSERT":
                    ins = safe_dxf(entity, "insert")
                    if ins is not None:
                        all_pts.append(Vec2(ins[0], ins[1]))
                elif et == "DIMENSION":
                    for attr in ("defpoint", "defpoint2", "defpoint3", "text_midpoint"):
                        v = safe_dxf(entity, attr, None)
                        if v is not None:
                            all_pts.append(Vec2(v[0], v[1]))
                else:
                    bbox = entity_bbox(entity)
                    if bbox is not None:
                        all_pts.append(bbox[0])
                        all_pts.append(bbox[1])
                        all_pts.append(Vec2(bbox[0].x, bbox[1].y))
                        all_pts.append(Vec2(bbox[1].x, bbox[0].y))
            except Exception:
                continue

        if not all_pts:
            return

        minx = min(p.x for p in all_pts)
        miny = min(p.y for p in all_pts)
        offset = Vec2(-minx, -miny)

        for entity in self.selected_entities:
            move_entity(entity, offset)

        self.modified = True
        self._render()
        self._status(f"Allineato a origine (offset: {offset.x:.2f}, {offset.y:.2f})")

    # ── Render ────────────────────────────────────────────────────────

    def _render(self) -> None:
        self.canvas.delete("all")
        if self.doc is None:
            self._draw_axes()
            self._populate_text_list()
            return
        self._draw_grid()
        self._draw_axes()
        self._draw_entities()
        self._draw_selection_highlight()
        self._populate_text_list()

    def _draw_grid(self) -> None:
        w = self.canvas.winfo_width() or 800
        h = self.canvas.winfo_height() or 600
        self.view.width = w
        self.view.height = h

        spacing = 10 ** math.ceil(math.log10(100 / self.view.scale))
        if spacing * self.view.scale < 10:
            spacing *= 10

        wx1, wy1 = self.view.screen_to_world(0, 0)
        wx2, wy2 = self.view.screen_to_world(w, h)

        xstart = math.floor(min(wx1, wx2) / spacing) * spacing
        ystart = math.floor(min(wy1, wy2) / spacing) * spacing
        xend = math.ceil(max(wx1, wx2) / spacing) * spacing
        yend = math.ceil(max(wy1, wy2) / spacing) * spacing

        for x in range(int(xstart), int(xend) + 1, int(spacing)):
            sx, _ = self.view.world_to_screen(x, 0)
            if 0 <= sx <= w:
                self.canvas.create_line(
                    sx, 0, sx, h,
                    fill=GRID_COLOR, tags=("grid",), width=1
                )

        for y in range(int(ystart), int(yend) + 1, int(spacing)):
            _, sy = self.view.world_to_screen(0, y)
            if 0 <= sy <= h:
                self.canvas.create_line(
                    0, sy, w, sy,
                    fill=GRID_COLOR, tags=("grid",), width=1
                )

    def _draw_axes(self) -> None:
        w = self.canvas.winfo_width() or 800
        h = self.canvas.winfo_height() or 600

        ox, oy = self.view.world_to_screen(0, 0)
        arrow_size = 12
        axis_len = min(w, h) * 0.3

        # Positive X axis
        x_end = min(ox + axis_len, w - 10)
        if 0 <= ox <= w:
            self.canvas.create_line(
                ox, oy, x_end, oy,
                fill="#FF4444", width=2, tags=("axes",)
            )
            self.canvas.create_polygon(
                x_end, oy, x_end - arrow_size, oy - 5,
                x_end - arrow_size, oy + 5,
                fill="#FF4444", outline="", tags=("axes",)
            )
            self.canvas.create_text(
                x_end + 10, oy, text="X",
                fill="#FF4444", font=("Segoe UI", 10, "bold"),
                anchor=tk.W, tags=("axes",)
            )

        # Positive Y axis
        y_end = min(oy - axis_len, 10)
        if 0 <= oy <= h:
            self.canvas.create_line(
                ox, oy, ox, y_end,
                fill="#44FF44", width=2, tags=("axes",)
            )
            self.canvas.create_polygon(
                ox, y_end, ox - 5, y_end + arrow_size,
                ox + 5, y_end + arrow_size,
                fill="#44FF44", outline="", tags=("axes",)
            )
            self.canvas.create_text(
                ox, y_end - 10, text="Y",
                fill="#44FF44", font=("Segoe UI", 10, "bold"),
                anchor=tk.S, tags=("axes",)
            )

        # Origin dot
        if 0 <= ox <= w and 0 <= oy <= h:
            self.canvas.create_oval(
                ox - 4, oy - 4, ox + 4, oy + 4,
                fill="#FFFFFF", outline="", tags=("axes",)
            )
            self.canvas.create_text(
                ox + 8, oy + 6, text="(0,0)",
                fill="#888888", font=("Segoe UI", 7),
                anchor=tk.NW, tags=("axes",)
            )

        # Negative axes (dashed)
        x_neg = max(ox - axis_len, 10)
        if 0 <= ox <= w:
            self.canvas.create_line(
                ox, oy, x_neg, oy,
                fill="#FF4444", width=1, dash=(3, 3), tags=("axes",)
            )
        y_neg = min(oy + axis_len, h - 10)
        if 0 <= oy <= h:
            self.canvas.create_line(
                ox, oy, ox, y_neg,
                fill="#44FF44", width=1, dash=(3, 3), tags=("axes",)
            )

    def _draw_entity(self, entity: Any) -> None:
        if self.doc is None:
            return
        et = entity.dxftype()
        selected = str(safe_dxf(entity, "handle", "")) in self.selection_handles

        if selected:
            color = HIGHLIGHT_COLOR
        else:
            entity_aci = safe_dxf(entity, "color", 256)
            if entity_aci is None or entity_aci == 256:
                # BYLAYER: use layer color
                color = ENTITY_COLOR
                layer = safe_dxf(entity, "layer", None)
                if layer and self.doc and self.doc.layers.has_entry(layer):
                    try:
                        lc = self.doc.layers.get(layer).dxf.color
                        if lc is not None and lc not in (0, 256):
                            color = aci_to_hex(lc)
                    except Exception:
                        pass
            elif entity_aci == 0:
                # BYBLOCK
                color = ENTITY_COLOR
            else:
                color = aci_to_hex(entity_aci)

        try:
            if et == "LINE":
                s = safe_dxf(entity, "start")
                e = safe_dxf(entity, "end")
                if s is not None and e is not None:
                    x1, y1 = self.view.world_to_screen(s[0], s[1])
                    x2, y2 = self.view.world_to_screen(e[0], e[1])
                    self.canvas.create_line(
                        x1, y1, x2, y2, fill=color, width=1, tags=("entity",)
                    )
            elif et in ("XLINE", "RAY"):
                start = safe_dxf(entity, "start")
                uv = safe_dxf(entity, "unit_vector")
                if start is not None and uv is not None:
                    p0 = Vec2(start[0], start[1])
                    dv = Vec2(uv[0], uv[1])
                    extent = max(self.view.width, self.view.height) / self.view.scale * 2
                    if et == "XLINE":
                        p1 = p0 - dv * extent
                        p2 = p0 + dv * extent
                    else:
                        p1 = p0
                        p2 = p0 + dv * extent
                    x1, y1 = self.view.world_to_screen(p1.x, p1.y)
                    x2, y2 = self.view.world_to_screen(p2.x, p2.y)
                    self.canvas.create_line(
                        x1, y1, x2, y2, fill=color, width=1, tags=("entity",)
                    )
            elif et == "LWPOLYLINE":
                pts = list(entity.get_points(format='xy'))
                if len(pts) >= 2:
                    coords = []
                    for p in pts:
                        sx, sy = self.view.world_to_screen(p[0], p[1])
                        coords.extend([sx, sy])
                    closed = safe_dxf(entity, "close", False) or (
                        len(pts) > 2 and is_polygon_closed(pts)
                    )
                    self.canvas.create_line(
                        *coords, fill=color, width=1, tags=("entity",), smooth=False,
                    )
                    if closed:
                        sx, sy = self.view.world_to_screen(pts[0][0], pts[0][1])
                        ex, ey = self.view.world_to_screen(pts[-1][0], pts[-1][1])
                        self.canvas.create_line(
                            sx, sy, ex, ey, fill=color, width=1, tags=("entity",)
                        )
                elif len(pts) == 1:
                    sx, sy = self.view.world_to_screen(pts[0][0], pts[0][1])
                    self.canvas.create_oval(
                        sx - 1, sy - 1, sx + 1, sy + 1,
                        fill=color, outline="", tags=("entity",)
                    )
            elif et == "POLYLINE":
                verts = list(getattr(entity, 'vertices', []))
                if len(verts) >= 2:
                    coords = []
                    for v in verts:
                        loc = safe_dxf(v, "location")
                        if loc is not None:
                            sx, sy = self.view.world_to_screen(loc[0], loc[1])
                            coords.extend([sx, sy])
                    if len(coords) >= 4:
                        self.canvas.create_line(
                            *coords, fill=color, width=1, tags=("entity",)
                        )
            elif et == "CIRCLE":
                c = safe_dxf(entity, "center")
                r = safe_dxf(entity, "radius")
                if c is not None and r is not None:
                    cx, cy = self.view.world_to_screen(c[0], c[1])
                    sr = r * self.view.scale
                    if sr > 1:
                        self.canvas.create_oval(
                            cx - sr, cy - sr, cx + sr, cy + sr,
                            outline=color, fill="", width=1, tags=("entity",)
                        )
                    else:
                        self.canvas.create_oval(
                            cx - 1, cy - 1, cx + 1, cy + 1,
                            fill=color, outline="", tags=("entity",)
                        )
            elif et == "ARC":
                c = safe_dxf(entity, "center")
                r = safe_dxf(entity, "radius")
                sa = safe_dxf(entity, "start_angle", 0) or 0
                ea = safe_dxf(entity, "end_angle", 360) or 360
                if c is not None and r is not None:
                    cx, cy = self.view.world_to_screen(c[0], c[1])
                    sr = r * self.view.scale
                    if sr > 1:
                        start_deg = sa
                        extent_deg = ea - sa
                        if extent_deg < 0:
                            extent_deg += 360
                        self.canvas.create_arc(
                            cx - sr, cy - sr, cx + sr, cy + sr,
                            start=90 - start_deg,
                            extent=-extent_deg if tkinter_arc_direction() else extent_deg,
                            outline=color, fill="", width=1,
                            style=tk.ARC, tags=("entity",)
                        )
                    else:
                        self.canvas.create_oval(
                            cx - 1, cy - 1, cx + 1, cy + 1,
                            fill=color, outline="", tags=("entity",)
                        )
            elif et == "TEXT":
                ins = safe_dxf(entity, "insert")
                text = safe_dxf(entity, "text", "")
                height = safe_dxf(entity, "height", 10) or 10
                rot = safe_dxf(entity, "rotation", 0) or 0
                if ins is not None and text:
                    sx, sy = self.view.world_to_screen(ins[0], ins[1])
                    font_size = max(8, min(48, int(height * self.view.scale * 1.2)))
                    self.canvas.create_text(
                        sx, sy, text=text, fill=color,
                        font=("Courier New", font_size),
                        angle=-rot, anchor=tk.SW, tags=("entity",)
                    )
            elif et == "MTEXT":
                ins = safe_dxf(entity, "insert")
                text = getattr(entity, 'text', safe_dxf(entity, 'text', ''))
                height = safe_dxf(entity, "char_height", 10) or 10
                rot = safe_dxf(entity, "rotation", 0) or 0
                if ins is not None and text:
                    sx, sy = self.view.world_to_screen(ins[0], ins[1])
                    font_size = max(8, min(48, int(height * self.view.scale * 1.2)))
                    display_text = clean_mtext(text)[:200] + ("..." if len(text) > 200 else "")
                    self.canvas.create_text(
                        sx, sy, text=display_text, fill=color,
                        font=("Courier New", font_size),
                        angle=-rot, anchor=tk.SW, tags=("entity",)
                    )
            elif et == "POINT":
                loc = safe_dxf(entity, "location")
                if loc is not None:
                    sx, sy = self.view.world_to_screen(loc[0], loc[1])
                    self.canvas.create_oval(
                        sx - 2, sy - 2, sx + 2, sy + 2,
                        fill=color, outline="", tags=("entity",)
                    )
            elif et == "ELLIPSE":
                c = safe_dxf(entity, "center")
                ma = safe_dxf(entity, "major_axis")
                ratio = safe_dxf(entity, "ratio", 1) or 1
                if c is not None and ma is not None:
                    cx, cy = self.view.world_to_screen(c[0], c[1])
                    major_len = Vec2(ma[0], ma[1]).magnitude
                    sr_a = major_len * self.view.scale
                    sr_b = sr_a * ratio
                    if sr_a > 1 and sr_b > 1:
                        self.canvas.create_oval(
                            cx - sr_a, cy - sr_b, cx + sr_a, cy + sr_b,
                            outline=color, fill="", width=1, tags=("entity",)
                        )
                    else:
                        self.canvas.create_oval(
                            cx - 1, cy - 1, cx + 1, cy + 1,
                            fill=color, outline="", tags=("entity",)
                        )
            elif et == "SOLID":
                pts = []
                for attr in ("vtx0", "vtx1", "vtx2", "vtx3"):
                    v = safe_dxf(entity, attr)
                    if v is not None:
                        pts.append(v)
                if len(pts) >= 3:
                    coords = []
                    for p in pts:
                        sx, sy = self.view.world_to_screen(p[0], p[1])
                        coords.extend([sx, sy])
                    self.canvas.create_polygon(
                        *coords, fill=color, outline=color, width=1, tags=("entity",)
                    )
            elif et == "3DFACE":
                pts = []
                for attr in ("vtx0", "vtx1", "vtx2", "vtx3"):
                    v = safe_dxf(entity, attr)
                    if v is not None:
                        pts.append(v)
                if len(pts) >= 3:
                    coords = []
                    for p in pts:
                        sx, sy = self.view.world_to_screen(p[0], p[1])
                        coords.extend([sx, sy])
                    self.canvas.create_polygon(
                        *coords, outline=color, fill="", width=1, tags=("entity",)
                    )
            elif et == "HATCH":
                pts_list = list(entity.paths) if hasattr(entity, 'paths') else []
                sp = safe_dxf(entity, "solid_fill", False)
                hatch_color = color if selected else HATCH_COLOR
                if sp or safe_dxf(entity, "pattern_name", ""):
                    for path in pts_list:
                        path_edges = getattr(path, 'edges', None)
                        if path_edges:
                            boundary_pts = []
                            for edge in path_edges:
                                try:
                                    if hasattr(edge, 'start') and hasattr(edge, 'end'):
                                        s = edge.start
                                        e_pt = edge.end
                                        if hasattr(s, 'x'):
                                            boundary_pts.append((s.x, s.y))
                                        if hasattr(e_pt, 'x'):
                                            boundary_pts.append((e_pt.x, e_pt.y))
                                    elif hasattr(edge, 'center') and hasattr(edge, 'radius'):
                                        c_edge = edge.center
                                        r_edge = edge.radius
                                        sa_edge = getattr(edge, 'start_angle', 0)
                                        ea_edge = getattr(edge, 'end_angle', 360)
                                        if hasattr(c_edge, 'x'):
                                            n = 20
                                            if callable(sa_edge):
                                                sa_edge = sa_edge()
                                            if callable(ea_edge):
                                                ea_edge = ea_edge()
                                            for i in range(n + 1):
                                                angle = sa_edge + (ea_edge - sa_edge) * i / n
                                                boundary_pts.append((
                                                    c_edge.x + r_edge * math.cos(math.radians(angle)),
                                                    c_edge.y + r_edge * math.sin(math.radians(angle)),
                                                ))
                                except Exception:
                                    pass
                            if boundary_pts:
                                screen_coords = []
                                for bx, by in boundary_pts:
                                    sx, sy = self.view.world_to_screen(bx, by)
                                    screen_coords.extend([sx, sy])
                                if len(screen_coords) >= 6:
                                    self.canvas.create_polygon(
                                        *screen_coords, fill=hatch_color,
                                        outline=hatch_color if not selected else HIGHLIGHT_COLOR,
                                        width=0, stipple="gray25", tags=("entity",)
                                    )
            elif et == "DIMENSION":
                dp = safe_dxf(entity, "defpoint")
                dp2 = safe_dxf(entity, "defpoint2")
                dp3 = safe_dxf(entity, "defpoint3")
                text_display = safe_dxf(entity, "text", "") or str(safe_dxf(entity, "measurement", ""))
                dim_color = DIM_COLOR if not selected else HIGHLIGHT_COLOR
                if dp is not None and dp2 is not None:
                    x1, y1 = self.view.world_to_screen(dp[0], dp[1])
                    x2, y2 = self.view.world_to_screen(dp2[0], dp2[1])
                    self.canvas.create_line(
                        x1, y1, x2, y2, fill=dim_color, width=1, tags=("entity",)
                    )
                    if dp3 is not None:
                        x3, y3 = self.view.world_to_screen(dp3[0], dp3[1])
                        self.canvas.create_line(
                            x1, y1, x3, y3, fill=dim_color, width=1, dash=(3, 3), tags=("entity",)
                        )
                        self.canvas.create_line(
                            x2, y2, x3, y3, fill=dim_color, width=1, dash=(3, 3), tags=("entity",)
                        )
                    if text_display:
                        mx = (x1 + x2) / 2
                        my = (y1 + y2) / 2 - 12
                        self.canvas.create_text(
                            mx, my, text=str(text_display),
                            fill=dim_color, font=("Courier New", 9), tags=("entity",)
                        )
            elif et == "SPLINE":
                ctrl = safe_dxf(entity, "control_points", None)
                if ctrl is not None and len(ctrl) >= 2:
                    coords = []
                    for p in ctrl:
                        if hasattr(p, 'x'):
                            sx, sy = self.view.world_to_screen(p.x, p.y)
                        else:
                            sx, sy = self.view.world_to_screen(p[0], p[1])
                        coords.extend([sx, sy])
                    self.canvas.create_line(
                        *coords, fill=color, width=1, smooth=True, tags=("entity",)
                    )
            elif et == "INSERT":
                ins = safe_dxf(entity, "insert")
                name = safe_dxf(entity, "name", "")
                rot = safe_dxf(entity, "rotation", 0) or 0
                if ins is not None:
                    sx, sy = self.view.world_to_screen(ins[0], ins[1])
                    # Small cross marker instead of block name text
                    self.canvas.create_line(
                        sx - 3, sy - 3, sx + 3, sy + 3,
                        fill=color, width=1, tags=("entity",)
                    )
                    self.canvas.create_line(
                        sx - 3, sy + 3, sx + 3, sy - 3,
                        fill=color, width=1, tags=("entity",)
                    )
            else:
                loc = get_location(entity)
                sx, sy = self.view.world_to_screen(loc.x, loc.y)
                self.canvas.create_text(
                    sx, sy, text=f"?{et[:4]}", fill="#888888",
                    font=("Courier New", 7), tags=("entity",)
                )
        except Exception as exc:
            err_key = f"{et}:{exc}"
            if err_key not in self._render_errors:
                self._render_errors.add(err_key)
                print(f"Render error {err_key}")

    def _draw_entities(self) -> None:
        for entity in self._get_visible_entities():
            self._draw_entity(entity)

    def _draw_selection_highlight(self) -> None:
        for entity in self.selected_entities:
            bbox = entity_bbox(entity)
            if bbox is not None:
                x1, y1 = self.view.world_to_screen(bbox[0].x, bbox[0].y)
                x2, y2 = self.view.world_to_screen(bbox[1].x, bbox[1].y)
                self.canvas.create_rectangle(
                    x1, y1, x2, y2,
                    outline=SELECT_COLOR, width=2, dash=(4, 4),
                    tags=("selection",)
                )
            loc = get_location(entity)
            sx, sy = self.view.world_to_screen(loc.x, loc.y)
            self.canvas.create_rectangle(
                sx - HANDLE_SIZE, sy - HANDLE_SIZE,
                sx + HANDLE_SIZE, sy + HANDLE_SIZE,
                fill=SELECT_COLOR, outline="white", width=1,
                tags=("selection",)
            )

            # Resize handle for TEXT/MTEXT (top-center)
            et = entity.dxftype()
            if et in ("TEXT", "MTEXT"):
                ins = safe_dxf(entity, "insert")
                h_val = safe_dxf(entity, "height") or safe_dxf(entity, "char_height") or 10
                if ins is not None:
                    rx, ry = self.view.world_to_screen(ins[0], ins[1] + h_val)
                    self.canvas.create_rectangle(
                        rx - HANDLE_SIZE - 2, ry - HANDLE_SIZE - 2,
                        rx + HANDLE_SIZE + 2, ry + HANDLE_SIZE + 2,
                        fill="#FF8800", outline="white", width=1,
                        tags=("selection",)
                    )

    # ── Interaction ───────────────────────────────────────────────────

    def _on_lbutton_down(self, event: tk.Event) -> None:
        self.canvas.focus_set()
        wx, wy = self.view.screen_to_world(event.x, event.y)

        # Line drawing mode
        if self.draw_line_mode:
            if self.line_start is None:
                self.line_start = (wx, wy)
                self._status(f"Linea: primo punto ({wx:.2f},{wy:.2f}) - click secondo punto")
            else:
                x1, y1 = self.line_start
                self._add_line(x1, y1, wx, wy)
                self.line_start = None
            return

        # Text drawing mode
        if self.draw_text_mode:
            from tkinter import simpledialog
            text = simpledialog.askstring("Inserisci testo", "Testo:",
                                           parent=self.master)
            if text:
                self._add_text(wx, wy, text)
            return

        if self.selected_entities:
            # Check resize handles first (TEXT/MTEXT top-center handle)
            for entity in self.selected_entities:
                et = entity.dxftype()
                if et in ("TEXT", "MTEXT"):
                    ins = safe_dxf(entity, "insert")
                    h_val = safe_dxf(entity, "height") or safe_dxf(entity, "char_height") or 10
                    if ins is not None:
                        rx, ry = self.view.world_to_screen(ins[0], ins[1] + h_val)
                        if abs(event.x - rx) < HANDLE_SIZE * 4 and abs(event.y - ry) < HANDLE_SIZE * 4:
                            self.resizing = True
                            self.resizing_entity = entity
                            self.resize_start_world = (wx, wy)
                            self.resize_initial_height = h_val
                            self._save_undo()
                            return
            # Check move handle
            for entity in self.selected_entities:
                loc = get_location(entity)
                sx, sy = self.view.world_to_screen(loc.x, loc.y)
                if abs(event.x - sx) < HANDLE_SIZE * 2 and abs(event.y - sy) < HANDLE_SIZE * 2:
                    self.moving = True
                    self.move_start_world = (wx, wy)
                    return

        hit = self._hit_test(wx, wy)
        if hit:
            h = str(safe_dxf(hit, "handle", ""))
            in_selection = h in self.selection_handles
            if not in_selection:
                self.selected_entities = [hit]
                self.selection_handles = {h}

            # For TEXT/MTEXT: click in upper third of bbox → resize instead of move
            if hit.dxftype() in ("TEXT", "MTEXT"):
                bbox = entity_bbox(hit)
                if bbox is not None:
                    y_top_third = bbox[0].y + (bbox[1].y - bbox[0].y) * 2 / 3
                    if wy >= y_top_third:
                        h_val = safe_dxf(hit, "height") or safe_dxf(hit, "char_height") or 10
                        self.resizing = True
                        self.resizing_entity = hit
                        self.resize_start_world = (wx, wy)
                        self.resize_initial_height = h_val
                        self._save_undo()
                        self._render()
                        return

            self.moving = True
            self.move_start_world = (wx, wy)
            self._render()
        else:
            self.drag_start = (event.x, event.y)
            self.drag_rect = (wx, wy, wx, wy)

    def _on_lbutton_move(self, event: tk.Event) -> None:
        if self.resizing and self.resizing_entity is not None:
            wx, wy = self.view.screen_to_world(event.x, event.y)
            ins = safe_dxf(self.resizing_entity, "insert")
            if ins is not None:
                new_height = max(1.0, wy - ins[1])
                et = self.resizing_entity.dxftype()
                if et == "MTEXT":
                    try:
                        self.resizing_entity.dxf.char_height = new_height
                    except Exception:
                        pass
                else:
                    try:
                        self.resizing_entity.dxf.height = new_height
                    except Exception:
                        pass
                self.modified = True
                self._render()
            return

        if self.moving and self.move_start_world is not None:
            wx, wy = self.view.screen_to_world(event.x, event.y)
            dx = wx - self.move_start_world[0]
            dy = wy - self.move_start_world[1]
            if abs(dx) > 1e-6 or abs(dy) > 1e-6:
                self._save_undo()
                offset = Vec2(dx, dy)
                for entity in self.selected_entities:
                    move_entity(entity, offset)
                self.move_start_world = (wx, wy)
                self.modified = True
                self._render()
                self._status(f"Spostamento: ({dx:.2f}, {dy:.2f})")
                return

        if self.drag_start is not None:
            x1, y1 = self.drag_start
            x2, y2 = event.x, event.y
            if self.rect_id:
                self.canvas.delete(self.rect_id)
            self.rect_id = self.canvas.create_rectangle(
                x1, y1, x2, y2,
                outline=SELECT_COLOR, width=1, dash=(4, 4),
                fill="", tags=("rubber",)
            )

    def _on_lbutton_up(self, event: tk.Event) -> None:
        if self.resizing:
            self.resizing = False
            self.resizing_entity = None
            self.resize_start_world = None
            return

        if self.moving:
            self.moving = False
            self.move_start_world = None
            return

        if self.drag_start is not None:
            wx1, wy1 = self.view.screen_to_world(*self.drag_start)
            wx2, wy2 = self.view.screen_to_world(event.x, event.y)
            if abs(wx2 - wx1) > 1e-4 or abs(wy2 - wy1) > 1e-4:
                self._select_in_rect(wx1, wy1, wx2, wy2)
                count = len(self.selected_entities)
                self._status(f"Selezionate {count} entità")

            self.drag_start = None
            self.drag_rect = None
            if self.rect_id:
                self.canvas.delete(self.rect_id)
                self.rect_id = None

    def _on_rbutton_down(self, event: tk.Event) -> None:
        self._on_mbutton_down(event)

    def _on_rbutton_move(self, event: tk.Event) -> None:
        self._on_mbutton_move(event)

    def _on_rbutton_up(self, event: tk.Event) -> None:
        self.panning = False
        self.pan_start = None

    def _on_mbutton_down(self, event: tk.Event) -> None:
        self.panning = True
        self.pan_start = (event.x, event.y)

    def _on_mbutton_move(self, event: tk.Event) -> None:
        if self.panning and self.pan_start is not None:
            dx = event.x - self.pan_start[0]
            dy = event.y - self.pan_start[1]
            self.view.pan(-dx, dy)
            self.pan_start = (event.x, event.y)
            self._render()

    def _on_wheel(self, event: tk.Event) -> None:
        factor = 1.15 if event.delta > 0 else 0.85
        self.view.zoom_at(event.x, event.y, factor)
        self._render()

    def _on_motion(self, event: tk.Event) -> None:
        wx, wy = self.view.screen_to_world(event.x, event.y)
        self.last_motion = (event.x, event.y)
        self.coord_label.config(text=f"({wx:.2f}, {wy:.2f})")
        self._status(
            f"({wx:.2f}, {wy:.2f})  |  "
            f"Selezionate: {len(self.selected_entities)}  |  "
            f"Scala: {self.view.scale:.6f}"
        )

        # Live line preview (rubber-band)
        if self.draw_line_mode and self.line_start is not None:
            self.canvas.delete("preview")
            x1, y1 = self.view.world_to_screen(
                self.line_start[0], self.line_start[1]
            )
            self.canvas.create_line(
                x1, y1, event.x, event.y,
                fill="#FF8800", width=1, dash=(4, 4),
                tags=("preview",),
            )

    def _on_configure(self, event: tk.Event) -> None:
        self.view.width = event.width
        self.view.height = event.height
        self._render()

    def _fit_view(self) -> None:
        entities = self._get_visible_entities()
        if not entities:
            self.view.view_x = 0
            self.view.view_y = 0
            self.view.scale = 1.0
        else:
            self.view.fit_to(entities)
        self._render()

    # ── Text Entity Panel ─────────────────────────────────────────────

    def _populate_text_list(self) -> None:
        if getattr(self, '_populating', False) or not hasattr(self, 'text_listbox'):
            return
        self._populating = True
        self.text_listbox.delete(0, tk.END)
        self._text_entity_list = []
        selected_handles = set(self.selection_handles)
        for entity in self._get_visible_entities():
            et = entity.dxftype()
            if et in ("TEXT", "MTEXT"):
                if et == "MTEXT":
                    text = getattr(entity, 'text', safe_dxf(entity, 'text', ''))
                else:
                    text = safe_dxf(entity, "text", "")
                if text:
                    self._text_entity_list.append(entity)
                    display = clean_mtext(text).strip()[:80].replace('\n', ' ')
                    self.text_listbox.insert(tk.END, display)
        for i, entity in enumerate(self._text_entity_list):
            if str(safe_dxf(entity, "handle", "")) in selected_handles:
                self.text_listbox.selection_set(i)
        self._populating = False

    def _on_text_list_select(self, event: tk.Event) -> None:
        if getattr(self, '_populating', False):
            return
        sel = self.text_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        if 0 <= idx < len(self._text_entity_list):
            entity = self._text_entity_list[idx]
            self.selected_entities = [entity]
            self.selection_handles = {str(safe_dxf(entity, "handle", ""))}
            self._render()

    # ── Tab Management ──────────────────────────────────────────────────

    _TAB_KEYS = [
        'doc', 'msp', 'filepath', 'modified', 'view',
        'selected_entities', 'selection_handles',
        'undo_stack', 'redo_stack', 'clipboard',
        'filter_enabled', '_render_errors',
        'draw_line_mode', 'draw_text_mode', 'line_start',
        'save_counter',
    ]

    def _collect_state(self) -> dict:
        return {k: getattr(self, k) for k in self._TAB_KEYS}

    def _apply_state(self, state: dict) -> None:
        for k, v in state.items():
            setattr(self, k, v)

    def _default_tab_state(self) -> dict:
        return {
            'doc': None, 'msp': None, 'filepath': None, 'modified': False,
            'view': Viewport(),
            'selected_entities': [], 'selection_handles': set(),
            'undo_stack': [], 'redo_stack': [],
            'clipboard': [],
            'filter_enabled': False,
            '_render_errors': set(),
            'draw_line_mode': False, 'draw_text_mode': False, 'line_start': None,
            'save_counter': 0,
        }

    def _new_tab(self) -> None:
        if self._current_tab >= 0:
            self._tabs[self._current_tab] = self._collect_state()
        self.canvas.delete("preview")
        self.canvas.config(cursor="")
        self.drag_start = None
        self.moving = False
        self.resizing = False
        state = self._default_tab_state()
        self._tabs.append(state)
        self._apply_state(state)
        self._current_tab = len(self._tabs) - 1
        self._rebuild_tab_bar()
        self._update_title()
        self._render()
        self._status("Nuovo documento")

    def _switch_tab(self, idx: int) -> None:
        if idx == self._current_tab or idx < 0 or idx >= len(self._tabs):
            return
        self._tabs[self._current_tab] = self._collect_state()
        self.canvas.delete("preview")
        self.canvas.config(cursor="")
        self.drag_start = None
        self.drag_rect = None
        self.moving = False
        self.move_start_world = None
        self.resizing = False
        self.resizing_entity = None
        self.resize_start_world = None
        self.line_start = None
        self._apply_state(self._tabs[idx])
        self._current_tab = idx
        self._rebuild_tab_bar()
        self._update_title()
        self._render()
        self._status(f"Tab: {os.path.basename(self.filepath) if self.filepath else 'Nuovo'}")

    def _close_tab(self, idx: int) -> None:
        if len(self._tabs) <= 1:
            return
        if self._current_tab == idx:
            self._tabs[self._current_tab] = self._collect_state()
        self._tabs.pop(idx)
        if self._current_tab >= idx:
            self._current_tab = max(0, self._current_tab - 1)
        self.canvas.delete("preview")
        self.canvas.config(cursor="")
        self.drag_start = None
        self.moving = False
        self.resizing = False
        self._apply_state(self._tabs[self._current_tab])
        self._rebuild_tab_bar()
        self._update_title()
        self._render()

    def _rebuild_tab_bar(self) -> None:
        if self._current_tab >= 0:
            self._tabs[self._current_tab] = self._collect_state()
        for w in self.tab_bar.winfo_children():
            w.destroy()
        for i, tab in enumerate(self._tabs):
            path = tab.get('filepath')
            title = os.path.basename(path) if path else 'Nuovo'
            mod = tab.get('modified', False)
            display = f'* {title}' if mod else title
            is_active = (i == self._current_tab)
            bg = "#2A2A2A" if is_active else "#1A1A1A"
            fg = "#FFFFFF" if is_active else "#AAAAAA"
            tab_frame = tk.Frame(self.tab_bar, bg=bg, bd=0)
            tab_frame.pack(side=tk.LEFT, padx=(0, 0))
            lbl = tk.Label(tab_frame, text=display, bg=bg, fg=fg,
                           font=("Segoe UI", 8), padx=8, pady=2, cursor="hand2")
            lbl.pack(side=tk.LEFT)
            lbl.bind("<Button-1>", lambda e, idx=i: self._switch_tab(idx))
            close_btn = tk.Label(tab_frame, text="×", bg=bg, fg="#666666",
                                 font=("Segoe UI", 8, "bold"), padx=4, pady=2,
                                 cursor="hand2")
            close_btn.pack(side=tk.RIGHT)
            close_btn.bind("<Button-1>", lambda e, idx=i: self._close_tab(idx))
            close_btn.bind("<Enter>", lambda e, b=close_btn: b.config(fg="#FF4444"))
            close_btn.bind("<Leave>", lambda e, b=close_btn: b.config(fg="#666666"))
        add_btn = tk.Label(self.tab_bar, text="+", bg="#0D0D0D", fg="#888888",
                           font=("Segoe UI", 10, "bold"), padx=8, pady=2, cursor="hand2")
        add_btn.pack(side=tk.LEFT)
        add_btn.bind("<Button-1>", lambda e: self._new_tab())
        add_btn.bind("<Enter>", lambda e: add_btn.config(fg="#FFFFFF"))
        add_btn.bind("<Leave>", lambda e: add_btn.config(fg="#888888"))

    def _update_title(self) -> None:
        if self._current_tab >= 0 and self._current_tab < len(self._tabs):
            tab = self._tabs[self._current_tab]
            path = tab.get('filepath')
            title = os.path.basename(path) if path else 'nessun file'
            self.master.title(f"DXF Editor - [{title}]")

    def _status(self, msg: str) -> None:
        self.status.config(text=msg)
