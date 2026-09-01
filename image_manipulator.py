from __future__ import annotations

import os
import io
import ctypes
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
import json
import webbrowser
from dataclasses import dataclass
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageOps, ImageTk

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    RootWindow = TkinterDnD.Tk
except ImportError:
    DND_FILES = None
    RootWindow = tk.Tk


APP_NAME = "Grove Swift Image Tools"
APP_VERSION = "1.0.0"
GITHUB_REPOSITORY = "grahamlgrove/swift-image-tools"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"
RELEASES_URL = f"https://github.com/{GITHUB_REPOSITORY}/releases/latest"
SUPPORTED = {
    ".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".avif", ".heic", ".heif",
    ".jxl", ".jp2", ".j2k", ".psd", ".exr", ".hdr", ".tga", ".ico", ".pcx", ".ppm", ".pgm", ".pnm",
    ".cr2", ".cr3", ".dng", ".nef", ".nrw", ".arw", ".srf", ".sr2", ".raf", ".orf", ".rw2", ".pef", ".srw", ".3fr"
}
RAW_EXTENSIONS = {".cr2", ".cr3", ".dng", ".nef", ".nrw", ".arw", ".srf", ".sr2", ".raf", ".orf", ".rw2", ".pef", ".srw", ".3fr"}
FORMAT_EXT = {
    "Original": "", "JPEG": ".jpg", "PNG": ".png", "WebP": ".webp", "TIFF": ".tiff",
    "BMP": ".bmp", "AVIF": ".avif", "GIF": ".gif", "JPEG 2000": ".jp2",
    "JPEG XL": ".jxl", "Photoshop PSD": ".psd", "OpenEXR": ".exr", "TGA": ".tga"
}
WIDTH_PRESETS = (2048, 1920, 1600, 1280, 800, 512, 256, 128, 64)
PRESETS = {
    "No resize": None,
    "HD · 1280 × 720": (1280, 720),
    "Full HD · 1920 × 1080": (1920, 1080),
    "4K · 3840 × 2160": (3840, 2160),
    "Instagram square · 1080 × 1080": (1080, 1080),
    "Instagram portrait · 1080 × 1350": (1080, 1350),
    "Facebook cover · 1640 × 624": (1640, 624),
    "Email large · 1600 × 1200": (1600, 1200),
    "Email small · 800 × 600": (800, 600),
    "Custom": "custom",
}


def app_dir() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def find_magick() -> str | None:
    candidates = [
        app_dir() / "ImageMagick" / "magick.exe",
        Path(sys.executable).resolve().parent / "ImageMagick" / "magick.exe",
        Path(__file__).resolve().parent / "ImageMagick" / "magick.exe",
    ]
    for item in candidates:
        if item.exists():
            return str(item)
    return shutil.which("magick")


def version_tuple(value: str) -> tuple[int, ...]:
    numbers = re.findall(r"\d+", value)
    return tuple(int(number) for number in numbers[:4]) or (0,)


def installed_edition() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(hive, r"Software\Grove Swift Image Tools") as key:
                    if winreg.QueryValueEx(key, "InstallType")[0] == "MSI":
                        return True
            except OSError:
                continue
        return False
    except OSError:
        return False


def unique_output(path: Path, source: Path) -> Path:
    if path.resolve() != source.resolve() and not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    number = 2
    candidate = path.with_name(f"{stem}_{number}{suffix}")
    while candidate.exists() or candidate.resolve() == source.resolve():
        number += 1
        candidate = path.with_name(f"{stem}_{number}{suffix}")
    return candidate


@dataclass
class CropFraction:
    left: float
    top: float
    right: float
    bottom: float


@dataclass
class ImageEditSettings:
    crop: CropFraction
    output_width: int = 800
    resize_enabled: bool = False


class CropCanvas(tk.Canvas):
    def __init__(self, master, on_crop_changed=None, **kwargs):
        super().__init__(master, bg="#171a20", highlightthickness=0, cursor="crosshair", **kwargs)
        self.image: Image.Image | None = None
        self.source_size = (1, 1)
        self.photo = None
        self.image_box = (0, 0, 1, 1)
        self.crop: CropFraction | None = None
        self.on_crop_changed = on_crop_changed
        self.start = None
        self.start_crop_value: CropFraction | None = None
        self.drag_mode = "new"
        self.bind("<Configure>", lambda _e: self.redraw())
        self.bind("<Button-1>", self.start_crop)
        self.bind("<B1-Motion>", self.drag_crop)
        self.bind("<ButtonRelease-1>", self.end_crop)
        self.bind("<Motion>", self.update_cursor)

    def set_image(self, image: Image.Image | None, crop: CropFraction | None = None, source_size=None):
        self.image = image
        self.source_size = source_size or (image.size if image else (1, 1))
        self.crop = CropFraction(**vars(crop)) if image and crop else (CropFraction(0, 0, 1, 1) if image else None)
        self.redraw()
        self.notify_crop()

    def set_crop_pixels(self, width: int, height: int):
        if not self.image:
            raise ValueError("Select an image first.")
        source_width, source_height = self.source_size
        if width < 1 or height < 1 or width > source_width or height > source_height:
            raise ValueError("The selection must fit inside the original image.")
        fw, fh = width / source_width, height / source_height
        self.crop = CropFraction((1-fw)/2, (1-fh)/2, (1+fw)/2, (1+fh)/2)
        self.redraw()
        self.notify_crop()

    def set_crop_height_pixels(self, height: int):
        source_height = self.source_size[1]
        if not self.image or not self.crop or height < 1 or height > source_height:
            raise ValueError("The calculated selection height must fit inside the image.")
        fraction = height / source_height
        centre = (self.crop.top + self.crop.bottom) / 2
        top = max(0.0, min(centre - fraction / 2, 1.0 - fraction))
        self.crop = CropFraction(self.crop.left, top, self.crop.right, top + fraction)
        self.redraw(); self.notify_crop()

    def set_crop_width_pixels(self, width: int):
        source_width = self.source_size[0]
        if not self.image or not self.crop or width < 1 or width > source_width:
            raise ValueError("The selected width must fit inside the image.")
        fraction = width / source_width
        centre = (self.crop.left + self.crop.right) / 2
        left = max(0.0, min(centre - fraction / 2, 1.0 - fraction))
        self.crop = CropFraction(left, self.crop.top, left + fraction, self.crop.bottom)
        self.redraw(); self.notify_crop()

    def notify_crop(self):
        if self.on_crop_changed:
            self.on_crop_changed(self.crop)

    def redraw(self):
        self.delete("all")
        if not self.image:
            self.create_text(self.winfo_width() / 2, self.winfo_height() / 2,
                             text="Select an image to preview", fill="#a7adb8", font=("Segoe UI", 13))
            return
        cw, ch = max(self.winfo_width(), 20), max(self.winfo_height(), 20)
        preview = self.image.copy()
        preview.thumbnail((max(cw - 24, 1), max(ch - 24, 1)), Image.Resampling.LANCZOS)
        self.photo = ImageTk.PhotoImage(preview)
        x, y = (cw - preview.width) // 2, (ch - preview.height) // 2
        self.image_box = (x, y, x + preview.width, y + preview.height)
        self.create_image(x, y, image=self.photo, anchor="nw", tags="preview")
        if self.crop:
            x1 = x + self.crop.left * preview.width
            y1 = y + self.crop.top * preview.height
            x2 = x + self.crop.right * preview.width
            y2 = y + self.crop.bottom * preview.height
            self.create_rectangle(x, y, x + preview.width, y1, fill="#000", stipple="gray50", width=0)
            self.create_rectangle(x, y2, x + preview.width, y + preview.height, fill="#000", stipple="gray50", width=0)
            self.create_rectangle(x, y1, x1, y2, fill="#000", stipple="gray50", width=0)
            self.create_rectangle(x2, y1, x + preview.width, y2, fill="#000", stipple="gray50", width=0)
            self.create_rectangle(x1, y1, x2, y2, outline="#4da3ff", width=3)
            for hx, hy in self.handle_points(x1, y1, x2, y2).values():
                self.create_rectangle(hx-5, hy-5, hx+5, hy+5, fill="#f7fbff", outline="#1677d2", width=2)

    @staticmethod
    def handle_points(x1, y1, x2, y2):
        mx, my = (x1+x2)/2, (y1+y2)/2
        return {"nw": (x1,y1), "n": (mx,y1), "ne": (x2,y1), "e": (x2,my),
                "se": (x2,y2), "s": (mx,y2), "sw": (x1,y2), "w": (x1,my)}

    def crop_box_pixels(self):
        ix1, iy1, ix2, iy2 = self.image_box
        c = self.crop
        return (ix1+c.left*(ix2-ix1), iy1+c.top*(iy2-iy1),
                ix1+c.right*(ix2-ix1), iy1+c.bottom*(iy2-iy1))

    def hit_test(self, x, y):
        if not self.crop: return "new"
        x1, y1, x2, y2 = self.crop_box_pixels()
        for name, (hx, hy) in self.handle_points(x1,y1,x2,y2).items():
            if abs(x-hx) <= 9 and abs(y-hy) <= 9: return name
        if x1 <= x <= x2 and y1 <= y <= y2: return "move"
        return "new"

    def update_cursor(self, event):
        cursors = {"nw":"size_nw_se", "se":"size_nw_se", "ne":"size_ne_sw", "sw":"size_ne_sw",
                   "n":"sb_v_double_arrow", "s":"sb_v_double_arrow", "e":"sb_h_double_arrow", "w":"sb_h_double_arrow",
                   "move":"fleur", "new":"crosshair"}
        try: self.configure(cursor=cursors[self.hit_test(event.x, event.y)])
        except tk.TclError: self.configure(cursor="crosshair")

    def clamp(self, x, y):
        x1, y1, x2, y2 = self.image_box
        return min(max(x, x1), x2), min(max(y, y1), y2)

    def start_crop(self, event):
        if self.image:
            self.start = self.clamp(event.x, event.y)
            self.drag_mode = self.hit_test(event.x, event.y)
            self.start_crop_value = CropFraction(**vars(self.crop)) if self.crop else None

    def drag_crop(self, event):
        if not self.start or not self.image:
            return
        sx, sy = self.start; ex, ey = self.clamp(event.x, event.y)
        ix1, iy1, ix2, iy2 = self.image_box
        fx, fy = (ex-sx)/(ix2-ix1), (ey-sy)/(iy2-iy1)
        if self.drag_mode == "move" and self.start_crop_value:
            c = self.start_crop_value; width, height = c.right-c.left, c.bottom-c.top
            left = min(max(c.left+fx, 0), 1-width); top = min(max(c.top+fy, 0), 1-height)
            self.crop = CropFraction(left, top, left+width, top+height)
        elif self.drag_mode == "new":
            left, right = sorted(((sx-ix1)/(ix2-ix1), (ex-ix1)/(ix2-ix1)))
            top, bottom = sorted(((sy-iy1)/(iy2-iy1), (ey-iy1)/(iy2-iy1)))
            self.crop = CropFraction(left, top, right, bottom)
        elif self.start_crop_value:
            c = self.start_crop_value
            left, top, right, bottom = c.left, c.top, c.right, c.bottom
            px, py = (ex-ix1)/(ix2-ix1), (ey-iy1)/(iy2-iy1)
            if "w" in self.drag_mode: left = min(px, right-.002)
            if "e" in self.drag_mode: right = max(px, left+.002)
            if "n" in self.drag_mode: top = min(py, bottom-.002)
            if "s" in self.drag_mode: bottom = max(py, top+.002)
            self.crop = CropFraction(max(0,left), max(0,top), min(1,right), min(1,bottom))
        self.redraw()
        self.notify_crop()

    def end_crop(self, _event):
        self.start = None
        self.start_crop_value = None
        if self.crop and (self.crop.right-self.crop.left < .005 or self.crop.bottom-self.crop.top < .005):
            self.crop = CropFraction(0, 0, 1, 1)
            self.redraw()
            self.notify_crop()


class Application:
    def __init__(self):
        self.root = RootWindow()
        self.root.title(f"{APP_NAME} {APP_VERSION}")
        self.root.geometry("1120x720")
        self.root.minsize(920, 620)
        self.files: list[Path] = []
        self.image_settings: dict[Path, ImageEditSettings] = {}
        self.preview_path: Path | None = None
        self.events = queue.Queue()
        self.same_dir = tk.BooleanVar(value=True)
        self.output_dir = tk.StringVar()
        self.format = tk.StringVar(value="Original")
        self.quality = tk.IntVar(value=100)
        self.original_dimensions = tk.StringVar(value="Original: —")
        self.selected_width = tk.StringVar(value="")
        self.selected_height = tk.StringVar(value="")
        self.output_width = tk.StringVar(value="800")
        self.width_preset = tk.StringVar(value="800")
        self.output_height = tk.StringVar(value="—")
        self.resize_enabled = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="Drop images here or choose files to begin")
        self.progress = tk.DoubleVar(value=0)
        self.build_ui()
        self.root.bind_all("<Button-1>", self.clear_input_focus, add="+")
        self.root.after(100, self.poll_events)
        self.root.after(2500, lambda: self.check_for_updates(silent=True))

    def clear_input_focus(self, event):
        input_widgets = (tk.Entry, tk.Text, tk.Spinbox, ttk.Entry, ttk.Spinbox, ttk.Combobox)
        if not isinstance(event.widget, input_widgets):
            self.root.focus_set()

    def build_ui(self):
        style = ttk.Style()
        style.theme_use("vista")
        style.configure("Title.TLabel", font=("Segoe UI Semibold", 18))
        style.configure("Section.TLabel", font=("Segoe UI Semibold", 10))
        main = ttk.Frame(self.root, padding=16)
        main.pack(fill="both", expand=True)
        title_row = ttk.Frame(main); title_row.pack(fill="x")
        ttk.Label(title_row, text=APP_NAME, style="Title.TLabel").pack(side="left")
        ttk.Label(title_row, text=f"Version {APP_VERSION}").pack(side="left", padx=(10,0), pady=(7,0))
        ttk.Button(title_row, text="Check for updates", command=self.check_for_updates).pack(side="right")
        ttk.Label(main, text="Quick, simple cropping, resizing and conversion — one image or a whole batch").pack(anchor="w", pady=(0, 12))
        body = ttk.Panedwindow(main, orient="horizontal")
        body.pack(fill="both", expand=True)
        left = ttk.Frame(body, padding=(0, 0, 12, 0)); right = ttk.Frame(body)
        body.add(left, weight=2); body.add(right, weight=3)

        ttk.Label(left, text="IMAGES", style="Section.TLabel").pack(anchor="w")
        list_frame = ttk.Frame(left); list_frame.pack(fill="both", expand=True, pady=(6, 8))
        self.listbox = tk.Listbox(list_frame, selectmode="extended", borderwidth=1, relief="solid",
                                  activestyle="none", font=("Segoe UI", 10))
        scroll = ttk.Scrollbar(list_frame, command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=scroll.set)
        self.listbox.pack(side="left", fill="both", expand=True); scroll.pack(side="right", fill="y")
        self.listbox.bind("<<ListboxSelect>>", self.select_preview)
        self.listbox.bind("<Button-3>", self.show_image_context_menu)
        self.image_context_menu = tk.Menu(self.root, tearoff=False)
        self.image_context_menu.add_command(label="Remove selected", command=self.remove_selected)
        if DND_FILES:
            self.listbox.drop_target_register(DND_FILES)
            self.listbox.dnd_bind("<<Drop>>", lambda e: self.add_paths(self.root.tk.splitlist(e.data)))
        buttons = ttk.Frame(left); buttons.pack(fill="x")
        ttk.Button(buttons, text="Add images…", command=self.choose_files).pack(side="left")
        ttk.Button(buttons, text="Add folder…", command=self.choose_folder).pack(side="left", padx=6)
        ttk.Button(buttons, text="Remove", command=self.remove_selected).pack(side="right")
        ttk.Button(buttons, text="Reset all settings", command=self.reset_all_settings).pack(side="right", padx=6)

        opts = ttk.LabelFrame(left, text=" Output ", padding=10); opts.pack(fill="x", pady=(14, 0))
        ttk.Radiobutton(opts, text="Same folder as each source image (default)", variable=self.same_dir,
                        value=True, command=self.toggle_output).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Radiobutton(opts, text="Use a different output folder", variable=self.same_dir,
                        value=False, command=self.toggle_output).grid(row=1, column=0, columnspan=3, sticky="w", pady=(3,0))
        self.output_entry = ttk.Entry(opts, textvariable=self.output_dir, state="disabled")
        self.output_entry.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(7, 0))
        self.output_button = ttk.Button(opts, text="Browse…", command=self.choose_output, state="disabled")
        self.output_button.grid(row=2, column=2, padx=(6, 0), pady=(7, 0))
        ttk.Label(opts, text="Format").grid(row=3, column=0, sticky="w", pady=(10, 3))
        ttk.Label(opts, text="Quality").grid(row=3, column=1, sticky="w", pady=(10, 3), padx=(8,0))
        ttk.Combobox(opts, textvariable=self.format, values=list(FORMAT_EXT), state="readonly", width=15).grid(row=4, column=0, sticky="ew")
        ttk.Spinbox(opts, from_=1, to=100, textvariable=self.quality, width=8).grid(row=4, column=1, sticky="w", padx=(8,0))
        opts.columnconfigure(0, weight=1); opts.columnconfigure(1, weight=1)

        preview_header = ttk.Frame(right); preview_header.pack(fill="x")
        ttk.Label(preview_header, text="PREVIEW & CROP", style="Section.TLabel").pack(side="left")
        ttk.Label(preview_header, textvariable=self.original_dimensions).pack(side="right")
        self.canvas = CropCanvas(right, on_crop_changed=self.crop_changed, height=330)
        self.canvas.pack(fill="both", expand=True, pady=(6, 10))
        self.canvas.bind("<Button-3>", self.show_preview_context_menu)
        self.preview_context_menu = tk.Menu(self.root, tearoff=False)
        self.preview_context_menu.add_command(label="Copy selection to clipboard", command=self.copy_selection_to_clipboard)

        crop = ttk.LabelFrame(right, text=" Crop selection and output size ", padding=10); crop.pack(fill="x")
        ttk.Label(crop, text="Selected width").grid(row=0, column=0, sticky="w")
        ttk.Label(crop, text="Selected height").grid(row=0, column=2, sticky="w")
        selected_width_entry = ttk.Entry(crop, textvariable=self.selected_width, width=8)
        selected_width_entry.grid(row=1, column=0, sticky="ew")
        selected_width_entry.bind("<Return>", self.selected_width_changed)
        selected_width_entry.bind("<FocusOut>", self.selected_width_changed)
        ttk.Label(crop, text="×").grid(row=1, column=1, padx=6)
        selected_height_entry = ttk.Entry(crop, textvariable=self.selected_height, width=8)
        selected_height_entry.grid(row=1, column=2, sticky="ew")
        selected_height_entry.bind("<Return>", self.selected_height_changed)
        selected_height_entry.bind("<FocusOut>", self.selected_height_changed)
        ttk.Button(crop, text="Create centred selection", command=self.create_typed_crop).grid(row=1, column=3, sticky="ew", padx=(10,0))
        ttk.Separator(crop, orient="vertical").grid(row=0, column=4, rowspan=3, sticky="ns", padx=14)
        ttk.Label(crop, text="Width preset").grid(row=0, column=5, sticky="w")
        ttk.Label(crop, text="Custom width").grid(row=0, column=6, sticky="w", padx=(8,0))
        ttk.Label(crop, text="Output height").grid(row=0, column=8, sticky="w")
        width_preset = ttk.Combobox(crop, textvariable=self.width_preset,
                                    values=[str(value) for value in WIDTH_PRESETS] + ["Custom"],
                                    state="disabled", width=8)
        width_preset.grid(row=1, column=5, sticky="ew")
        width_preset.bind("<<ComboboxSelected>>", self.output_width_preset_changed)
        out_width = ttk.Entry(crop, textvariable=self.output_width, width=9, state="disabled")
        out_width.grid(row=1, column=6, sticky="ew", padx=(8,0))
        out_width.bind("<KeyRelease>", self.output_width_changed)
        ttk.Label(crop, text="×").grid(row=1, column=7, padx=6)
        out_height = ttk.Entry(crop, textvariable=self.output_height, width=8)
        out_height.grid(row=1, column=8, sticky="ew")
        out_height.bind("<Return>", self.output_height_changed)
        out_height.bind("<FocusOut>", self.output_height_changed)
        self.output_width_entry = out_width
        self.output_height_entry = out_height
        self.width_preset_combo = width_preset
        ttk.Button(crop, text="Reset to whole image", command=self.reset_crop).grid(row=1, column=9, sticky="ew", padx=(10,0))
        ttk.Checkbutton(crop, text="Resize output", variable=self.resize_enabled, command=self.resize_toggle_changed).grid(row=2, column=5, columnspan=5, sticky="w", pady=(8,0))
        ttk.Label(crop, text="Drag inside to move. Drag any blue handle to resize. Select Custom once, then edit its width freely.", foreground="#666").grid(row=3, column=0, columnspan=10, sticky="w", pady=(8,0))
        crop.columnconfigure(0, weight=1); crop.columnconfigure(2, weight=1)
        crop.columnconfigure(5, weight=1); crop.columnconfigure(6, weight=1); crop.columnconfigure(8, weight=1)
        self.update_resize_control_state()

        footer = ttk.Frame(main); footer.pack(fill="x", pady=(12, 0))
        ttk.Label(footer, textvariable=self.status).pack(side="left")
        self.go = ttk.Button(footer, text="Convert images", command=self.start_processing)
        self.go.pack(side="right")
        self.copy_button = ttk.Button(footer, text="Copy selection to clipboard", command=self.copy_selection_to_clipboard)
        self.copy_button.pack(side="right", padx=(0, 8))
        ttk.Progressbar(footer, variable=self.progress, maximum=100, length=190).pack(side="right", padx=12)

    def show_preview_context_menu(self, event):
        if not self.preview_path or not self.canvas.crop:
            return
        try:
            self.preview_context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.preview_context_menu.grab_release()

    def choose_files(self):
        paths = filedialog.askopenfilenames(
            title="Choose images",
            filetypes=[("Images and camera RAW", "*.jpg *.jpeg *.png *.webp *.gif *.bmp *.tif *.tiff *.avif *.heic *.heif *.jxl *.jp2 *.j2k *.psd *.exr *.hdr *.tga *.ico *.cr2 *.cr3 *.dng *.nef *.nrw *.arw *.srf *.sr2 *.raf *.orf *.rw2 *.pef *.srw *.3fr"), ("All files", "*.*")])
        self.add_paths(paths)

    def choose_folder(self):
        folder = filedialog.askdirectory(title="Choose a folder of images")
        if folder:
            self.add_paths([folder])

    def add_paths(self, paths):
        added = []
        for raw in paths:
            path = Path(str(raw).strip("{}"))
            candidates = path.rglob("*") if path.is_dir() else [path]
            for item in candidates:
                if item.is_file() and item.suffix.lower() in SUPPORTED and item not in self.files:
                    self.files.append(item); added.append(item)
        self.refresh_list()
        if added and not self.preview_path:
            self.listbox.selection_set(0); self.select_preview()
        self.status.set(f"{len(self.files)} image{'s' if len(self.files) != 1 else ''} ready")

    def refresh_list(self):
        self.listbox.delete(0, "end")
        for p in self.files:
            self.listbox.insert("end", f"  {p.name}")

    def show_image_context_menu(self, event):
        if not self.files:
            return
        index = self.listbox.nearest(event.y)
        bbox = self.listbox.bbox(index)
        if not bbox or not (bbox[1] <= event.y <= bbox[1] + bbox[3]):
            return
        if index not in self.listbox.curselection():
            self.listbox.selection_clear(0, "end")
            self.listbox.selection_set(index)
            self.listbox.activate(index)
            self.select_preview()
        count = len(self.listbox.curselection())
        self.image_context_menu.entryconfigure(0, label=f"Remove selected ({count})")
        try:
            self.image_context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.image_context_menu.grab_release()

    def remove_selected(self):
        selected = set(self.listbox.curselection())
        removed = [p for i, p in enumerate(self.files) if i in selected]
        self.files = [p for i, p in enumerate(self.files) if i not in selected]
        for path in removed: self.image_settings.pop(path, None)
        self.preview_path = None; self.canvas.set_image(None); self.refresh_list()

    def select_preview(self, _event=None):
        selected = self.listbox.curselection()
        if not selected: return
        self.preview_path = self.files[selected[0]]
        try:
            preview, source_size = self.load_preview(self.preview_path)
            settings = self.image_settings.setdefault(
                self.preview_path, ImageEditSettings(CropFraction(0, 0, 1, 1), 800))
            self.output_width.set(str(settings.output_width))
            self.width_preset.set(str(settings.output_width) if settings.output_width in WIDTH_PRESETS else "Custom")
            self.resize_enabled.set(settings.resize_enabled)
            self.update_resize_control_state()
            self.canvas.set_image(preview, settings.crop, source_size)
            self.original_dimensions.set(f"Original: {source_size[0]} × {source_size[1]} px")
            self.status.set(f"{self.preview_path.name} · {source_size[0]} × {source_size[1]}")
        except Exception as exc:
            self.status.set(f"Could not preview {self.preview_path.name}: {exc}")

    def load_preview(self, path: Path):
        try:
            with Image.open(path) as im:
                image = ImageOps.exif_transpose(im).convert("RGBA")
            return image, image.size
        except Exception:
            magick = find_magick()
            if not magick: raise
            flags = subprocess.CREATE_NO_WINDOW
            source_size = self.read_source_size(path)
            preview_result = subprocess.run(
                [magick, str(path), "-auto-orient", "-thumbnail", "1800x1800>", "png:-"],
                capture_output=True, creationflags=flags)
            if preview_result.returncode:
                raise RuntimeError(preview_result.stderr.decode(errors="replace").strip())
            with Image.open(io.BytesIO(preview_result.stdout)) as im:
                preview = im.convert("RGBA")
            return preview, source_size

    def read_source_size(self, path: Path):
        try:
            with Image.open(path) as im:
                return ImageOps.exif_transpose(im).size
        except Exception:
            result = subprocess.run(
                [find_magick(), str(path), "-auto-orient", "-format", "%w,%h", "info:"],
                capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
            match = re.search(r"(\d+),(\d+)", result.stdout)
            if result.returncode or not match:
                raise RuntimeError(result.stderr.strip() or "ImageMagick could not read this image")
            return int(match.group(1)), int(match.group(2))

    def toggle_output(self):
        state = "disabled" if self.same_dir.get() else "normal"
        self.output_entry.configure(state=state); self.output_button.configure(state=state)

    def choose_output(self):
        folder = filedialog.askdirectory(title="Choose output folder")
        if folder: self.output_dir.set(folder)

    def crop_changed(self, crop):
        if not crop or not self.canvas.image:
            self.selected_width.set(""); self.selected_height.set(""); self.output_height.set("—")
            return
        source_width, source_height = self.canvas.source_size
        width = max(1, round((crop.right-crop.left) * source_width))
        height = max(1, round((crop.bottom-crop.top) * source_height))
        self.selected_width.set(str(width)); self.selected_height.set(str(height))
        if self.preview_path:
            settings = self.image_settings.setdefault(
                self.preview_path, ImageEditSettings(CropFraction(0, 0, 1, 1), 800))
            settings.crop = CropFraction(**vars(crop))
        self.calculate_output_height()

    def create_typed_crop(self):
        try:
            w, h = int(self.selected_width.get()), int(self.selected_height.get())
            if w < 1 or h < 1: raise ValueError
            self.canvas.set_crop_pixels(w, h)
            self.status.set(f"Created a centred {w} × {h} px selection")
        except ValueError:
            messagebox.showerror(APP_NAME, "Selected width and height must be positive whole numbers that fit inside the original image.")

    def calculate_output_height(self):
        try:
            selected_w, selected_h = int(self.selected_width.get()), int(self.selected_height.get())
            output_w = int(self.output_width.get())
            if min(selected_w, selected_h, output_w) < 1: raise ValueError
            self.output_height.set(str(max(1, round(output_w * selected_h / selected_w))))
        except ValueError:
            self.output_height.set("—")

    def output_width_changed(self, _event=None):
        try:
            value = int(self.output_width.get())
            if value < 1: raise ValueError
            if self.preview_path:
                settings = self.image_settings.setdefault(
                    self.preview_path, ImageEditSettings(CropFraction(0, 0, 1, 1), 800))
                settings.output_width = value
        except ValueError:
            pass
        self.calculate_output_height()

    def output_width_preset_changed(self, _event=None):
        value = self.width_preset.get()
        if value != "Custom":
            self.output_width.set(value)
            self.output_width_changed()
        self.update_resize_control_state()

    def resize_toggle_changed(self):
        if self.preview_path:
            settings = self.image_settings.setdefault(
                self.preview_path, ImageEditSettings(CropFraction(0, 0, 1, 1), 800))
            settings.resize_enabled = self.resize_enabled.get()
        self.update_resize_control_state()

    def update_resize_control_state(self):
        if not hasattr(self, "output_width_entry"): return
        resize_on = self.resize_enabled.get()
        self.width_preset_combo.configure(state="readonly" if resize_on else "disabled")
        self.output_width_entry.configure(state="normal" if resize_on and self.width_preset.get() == "Custom" else "disabled")
        self.output_height_entry.configure(state="normal" if resize_on else "disabled")

    def selected_width_changed(self, _event=None):
        try:
            width = int(self.selected_width.get())
            self.canvas.set_crop_width_pixels(width)
            self.status.set(f"Selection width adjusted to {width} px")
        except ValueError as exc:
            self.crop_changed(self.canvas.crop)
            if str(exc): self.status.set(str(exc))

    def selected_height_changed(self, _event=None):
        try:
            height = int(self.selected_height.get())
            self.canvas.set_crop_height_pixels(height)
            self.status.set(f"Selection height adjusted to {height} px")
        except ValueError as exc:
            self.crop_changed(self.canvas.crop)
            if str(exc): self.status.set(str(exc))

    def output_height_changed(self, _event=None):
        try:
            output_w, output_h = int(self.output_width.get()), int(self.output_height.get())
            selected_w = int(self.selected_width.get())
            if min(output_w, output_h, selected_w) < 1: raise ValueError
            new_selected_height = max(1, round(output_h * selected_w / output_w))
            self.canvas.set_crop_height_pixels(new_selected_height)
            self.status.set(f"Selection height adjusted to {new_selected_height} px")
        except ValueError as exc:
            self.calculate_output_height()
            if _event and str(exc): self.status.set(str(exc))

    def reset_crop(self):
        if self.canvas.image:
            self.canvas.crop = CropFraction(0, 0, 1, 1)
            self.canvas.redraw(); self.canvas.notify_crop()

    def reset_all_settings(self):
        for path in self.files:
            self.image_settings[path] = ImageEditSettings(CropFraction(0, 0, 1, 1), 800, False)
        if self.preview_path and self.canvas.image:
            self.output_width.set("800")
            self.width_preset.set("800")
            self.resize_enabled.set(False)
            self.update_resize_control_state()
            self.canvas.crop = CropFraction(0, 0, 1, 1)
            self.canvas.redraw(); self.canvas.notify_crop()
        self.status.set(f"Reset crop and output size for {len(self.files)} image(s)")

    def validate(self):
        if not self.files: raise ValueError("Add at least one image first.")
        if not self.same_dir.get() and not self.output_dir.get(): raise ValueError("Choose an output folder.")
        if not self.canvas.crop: raise ValueError("Select an image and crop area first.")
        if self.resize_enabled.get() and (int(self.output_width.get()) < 1 or int(self.output_height.get()) < 1):
            raise ValueError("Enter valid output dimensions.")
        if not find_magick(): raise ValueError("ImageMagick was not found. Run package.ps1 to create the bundled app.")

    def start_processing(self):
        try: self.validate()
        except (ValueError, TypeError):
            messagebox.showerror(APP_NAME, "Please check the image list, output folder, dimensions, and crop selection.")
            return
        self.go.configure(state="disabled"); self.progress.set(0)
        threading.Thread(target=self.process_all, daemon=True).start()

    def process_all(self):
        failures = []
        for index, source in enumerate(self.files):
            try:
                self.events.put(("status", f"Processing {source.name}…"))
                self.process_one(source)
            except Exception as exc:
                failures.append(f"{source.name}: {exc}")
            self.events.put(("progress", (index + 1) / len(self.files) * 100))
        self.events.put(("done", failures))

    def process_one(self, source: Path):
        out_dir = source.parent if self.same_dir.get() else Path(self.output_dir.get())
        out_dir.mkdir(parents=True, exist_ok=True)
        ext = FORMAT_EXT[self.format.get()] or source.suffix.lower()
        if self.format.get() == "Original" and source.suffix.lower() in RAW_EXTENSIONS:
            raise ValueError("Camera RAW is input-only; choose an output format such as TIFF, JPEG, PNG, or AVIF")
        args = [find_magick(), str(source), "-auto-orient"]
        settings = self.image_settings.get(source, ImageEditSettings(CropFraction(0, 0, 1, 1), 800))
        crop = settings.crop
        w, h = self.read_source_size(source)
        x = round(crop.left*w); y = round(crop.top*h)
        cw = max(1, round((crop.right-crop.left)*w)); ch = max(1, round((crop.bottom-crop.top)*h))
        output_width = settings.output_width if settings.resize_enabled else cw
        output_height = max(1, round(output_width * ch / cw)) if settings.resize_enabled else ch
        destination = unique_output(
            out_dir / f"{source.stem}_converted_{output_width}x{output_height}{ext}", source)
        args += ["-crop", f"{cw}x{ch}+{x}+{y}", "+repage"]
        if settings.resize_enabled:
            args += ["-resize", f"{settings.output_width}x"]
        if ext in {".jpg", ".jpeg", ".webp", ".avif", ".jxl", ".jp2", ".j2k"}:
            args += ["-quality", str(self.quality.get())]
        args += [str(destination)]
        env = os.environ.copy()
        bundled = str(Path(find_magick()).parent)
        env["PATH"] = bundled + os.pathsep + env.get("PATH", "")
        result = subprocess.run(args, capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW, env=env)
        if result.returncode:
            raise RuntimeError(result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "ImageMagick failed")

    def copy_selection_to_clipboard(self):
        if not self.preview_path or not self.canvas.crop:
            messagebox.showerror(APP_NAME, "Select an image first.")
            return
        self.copy_button.configure(state="disabled")
        self.status.set("Preparing selection for the clipboard…")
        source = self.preview_path
        threading.Thread(target=self.copy_selection_worker, args=(source,), daemon=True).start()

    def copy_selection_worker(self, source: Path):
        try:
            settings = self.image_settings.get(source, ImageEditSettings(CropFraction(0, 0, 1, 1), 800))
            crop = settings.crop
            width, height = self.read_source_size(source)
            x = round(crop.left * width); y = round(crop.top * height)
            crop_width = max(1, round((crop.right-crop.left) * width))
            crop_height = max(1, round((crop.bottom-crop.top) * height))
            output_width = settings.output_width if settings.resize_enabled else crop_width
            output_height = max(1, round(output_width * crop_height / crop_width)) if settings.resize_enabled else crop_height
            args = [find_magick(), str(source), "-auto-orient", "-crop",
                    f"{crop_width}x{crop_height}+{x}+{y}", "+repage"]
            if settings.resize_enabled:
                args += ["-resize", f"{settings.output_width}x"]
            args += ["BMP3:-"]
            env = os.environ.copy()
            bundled = str(Path(find_magick()).parent)
            env["PATH"] = bundled + os.pathsep + env.get("PATH", "")
            result = subprocess.run(args, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, env=env)
            if result.returncode or not result.stdout.startswith(b"BM"):
                raise RuntimeError(result.stderr.decode(errors="replace").strip() or "Could not create clipboard image")
            self.set_windows_clipboard_dib(result.stdout[14:])
            self.events.put(("copy_done", (True, f"Copied {output_width} × {output_height} px selection to the clipboard")))
        except Exception as exc:
            self.events.put(("copy_done", (False, str(exc))))

    @staticmethod
    def set_windows_clipboard_dib(dib_data: bytes):
        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32
        kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
        kernel32.GlobalAlloc.restype = ctypes.c_void_p
        kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
        user32.OpenClipboard.argtypes = [ctypes.c_void_p]
        user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
        user32.SetClipboardData.restype = ctypes.c_void_p
        handle = kernel32.GlobalAlloc(0x0002, len(dib_data))
        if not handle:
            raise RuntimeError("Windows could not allocate clipboard memory")
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            kernel32.GlobalFree(handle)
            raise RuntimeError("Windows could not lock clipboard memory")
        ctypes.memmove(pointer, dib_data, len(dib_data))
        kernel32.GlobalUnlock(handle)
        if not user32.OpenClipboard(None):
            kernel32.GlobalFree(handle)
            raise RuntimeError("The clipboard is currently busy; please try again")
        try:
            user32.EmptyClipboard()
            if not user32.SetClipboardData(8, handle):
                kernel32.GlobalFree(handle)
                raise RuntimeError("Windows could not place the image on the clipboard")
            handle = None
        finally:
            user32.CloseClipboard()

    def poll_events(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "status": self.status.set(value)
                elif kind == "progress": self.progress.set(value)
                elif kind == "copy_done":
                    self.copy_button.configure(state="normal")
                    success, message = value
                    self.status.set(message if success else f"Clipboard copy failed: {message}")
                    if not success:
                        messagebox.showerror(APP_NAME, f"Could not copy the selection:\n\n{message}")
                elif kind == "update_result":
                    self.handle_update_result(*value)
                elif kind == "launch_installer":
                    subprocess.Popen(["msiexec.exe", "/i", str(value)], creationflags=subprocess.CREATE_NO_WINDOW)
                    self.root.destroy()
                elif kind == "open_portable_update":
                    os.startfile(value)
                elif kind == "done":
                    self.go.configure(state="normal")
                    if value:
                        self.status.set(f"Finished with {len(value)} error(s)")
                        messagebox.showwarning(APP_NAME, "Some images could not be processed:\n\n" + "\n".join(value[:8]))
                    else:
                        self.status.set(f"Done — converted {len(self.files)} image(s)")
                        messagebox.showinfo(APP_NAME, "All images were processed successfully.")
        except queue.Empty: pass
        self.root.after(100, self.poll_events)

    def check_for_updates(self, silent=False):
        if not silent:
            self.status.set("Checking GitHub for updates…")
        threading.Thread(target=self.update_check_worker, args=(silent,), daemon=True).start()

    def update_check_worker(self, silent):
        try:
            request = urllib.request.Request(
                LATEST_RELEASE_API,
                headers={"Accept": "application/vnd.github+json", "User-Agent": f"{APP_NAME}/{APP_VERSION}"},
            )
            with urllib.request.urlopen(request, timeout=12) as response:
                release = json.load(response)
            latest = str(release.get("tag_name", "")).lstrip("vV")
            assets = release.get("assets", [])
            suffix = ".msi" if installed_edition() else ".zip"
            asset = next((item for item in assets if str(item.get("name", "")).lower().endswith(suffix)), None)
            self.events.put(("update_result", (silent, latest, asset, None)))
        except Exception as exc:
            self.events.put(("update_result", (silent, "", None, str(exc))))

    def handle_update_result(self, silent, latest, asset, error):
        if error:
            if not silent:
                self.status.set("Could not check for updates")
                messagebox.showerror(APP_NAME, f"The update check could not be completed.\n\n{error}")
            return
        if version_tuple(latest) <= version_tuple(APP_VERSION):
            if not silent:
                self.status.set(f"Version {APP_VERSION} is current")
                messagebox.showinfo(APP_NAME, f"You already have the latest version ({APP_VERSION}).")
            return
        if not asset:
            if messagebox.askyesno(APP_NAME, f"Version {latest} is available. Open the release page?"):
                webbrowser.open(RELEASES_URL)
            return
        edition = "installer" if installed_edition() else "portable package"
        if not messagebox.askyesno(APP_NAME, f"Version {latest} is available.\n\nDownload the {edition} now?"):
            return
        self.status.set(f"Downloading version {latest}…")
        threading.Thread(target=self.download_update_worker, args=(latest, asset), daemon=True).start()

    def download_update_worker(self, latest, asset):
        try:
            name = str(asset["name"])
            destination = Path(tempfile.gettempdir()) / name
            request = urllib.request.Request(asset["browser_download_url"], headers={"User-Agent": f"{APP_NAME}/{APP_VERSION}"})
            with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as output:
                shutil.copyfileobj(response, output)
            if installed_edition():
                self.events.put(("launch_installer", destination))
            else:
                self.events.put(("open_portable_update", destination))
                self.events.put(("status", f"Downloaded version {latest}; extract the ZIP to update the portable edition."))
        except Exception as exc:
            self.events.put(("update_result", (False, "", None, f"The update could not be downloaded.\n\n{exc}")))

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    Application().run()
