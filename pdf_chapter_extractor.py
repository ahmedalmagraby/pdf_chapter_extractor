"""
PDF Chapter Extractor
=====================
Extract chapter PDFs from a bookmarked source PDF via CLI or a desktop GUI.
"""

import argparse
import os
import queue
import re
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable


# ---------------------------------------------------------------------------
#  Data types
# ---------------------------------------------------------------------------

@dataclass
class ChapterStart:
    """Represents the beginning of a single chapter (bookmark)."""
    title: str
    start_page: int
    level: int


@dataclass
class ExtractionPlan:
    """Pre-computed plan showing what chapters will be extracted and page ranges."""
    chapters: list[dict] = field(default_factory=list)  # {title, start_page, end_page, pages_count, filename, idx}
    total_pages: int = 0
    level: int = 0


# ---------------------------------------------------------------------------
#  Lazy dependency loader
# ---------------------------------------------------------------------------

def _load_pypdf():
    """Import pypdf lazily so the module can be imported without the dependency."""
    try:
        from pypdf import PdfReader, PdfWriter
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "Missing dependency: pypdf. Install it with `pip install pypdf`."
        ) from exc
    return PdfReader, PdfWriter


# ---------------------------------------------------------------------------
#  Utility helpers
# ---------------------------------------------------------------------------

def sanitize_filename(name: str) -> str:
    """Return a filesystem-safe chapter filename stem."""
    if not name:
        return "untitled"
    safe_name = re.sub(r'[<>:"/\\|?*]', "_", name)
    safe_name = re.sub(r"\s+", " ", safe_name).strip()
    safe_name = safe_name[:150]
    return safe_name or "untitled_chapter"


def _unique_path(path: str) -> str:
    """If *path* exists, append an incrementing suffix until it is unique."""
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    counter = 2
    while True:
        candidate = f"{base}_{counter}{ext}"
        if not os.path.exists(candidate):
            return candidate
        counter += 1


def _log(message: str, logger: Callable[[str], None] | None = None) -> None:
    if logger:
        logger(message)
    else:
        print(message)


def open_folder(path: str) -> None:
    """Open a folder in the OS file manager."""
    path = os.path.realpath(path)
    if sys.platform == "win32":
        os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


# ---------------------------------------------------------------------------
#  Core bookmark logic
# ---------------------------------------------------------------------------

def find_chapter_bookmarks(
    outline_items: Iterable,
    reader,
    target_level: int,
    current_level: int = 0,
) -> list[ChapterStart]:
    """Recursively find outline destinations at a specific depth."""
    chapters: list[ChapterStart] = []
    for item in outline_items:
        if isinstance(item, list):
            if current_level < target_level:
                chapters.extend(
                    find_chapter_bookmarks(item, reader, target_level, current_level + 1)
                )
            continue
        if current_level != target_level:
            continue
        try:
            page_num = reader.get_destination_page_number(item)
            if page_num is not None:
                chapters.append(
                    ChapterStart(
                        title=str(getattr(item, "title", "Untitled Chapter")),
                        start_page=page_num,
                        level=current_level,
                    )
                )
        except Exception as exc:
            _log(f"  Warning: could not read bookmark '{getattr(item, 'title', 'unknown')}': {exc}")
    return chapters


def get_available_bookmark_levels(outline_items: Iterable, current_level: int = 0) -> set[int]:
    """Return all outline depths that contain page-linking destinations."""
    levels: set[int] = set()
    for item in outline_items:
        if isinstance(item, list):
            levels.update(get_available_bookmark_levels(item, current_level + 1))
        else:
            levels.add(current_level)
    return levels


def _get_preview_entries(outline_items: Iterable, reader, current_level: int = 0, max_depth: int = 3) -> list[dict]:
    """Return a flat list of bookmark entries (up to *max_depth*) for tree display."""
    entries: list[dict] = []
    for item in outline_items:
        if isinstance(item, list):
            if current_level < max_depth:
                entries.extend(_get_preview_entries(item, reader, current_level + 1, max_depth))
            continue
        title = str(getattr(item, "title", "Untitled"))
        try:
            page = reader.get_destination_page_number(item)
        except Exception:
            page = None
        entries.append({"title": title, "page": page, "level": current_level})
    return entries


# ---------------------------------------------------------------------------
#  Inspection
# ---------------------------------------------------------------------------

def inspect_pdf(pdf_path: str) -> dict:
    """Inspect a PDF and return bookmark metadata useful for UI and CLI."""
    PdfReader, _ = _load_pypdf()
    reader = PdfReader(pdf_path)
    outline = getattr(reader, "outline", None)
    has_outline = bool(outline)
    levels = sorted(get_available_bookmark_levels(outline)) if has_outline else []
    preview: list[dict] = []
    if has_outline:
        preview = _get_preview_entries(outline, reader)
    return {
        "pages": len(reader.pages),
        "has_outline": has_outline,
        "levels": levels,
        "preview": preview,
    }


# ---------------------------------------------------------------------------
#  Extraction plan (preview before writing)
# ---------------------------------------------------------------------------

def build_extraction_plan(
    pdf_path: str,
    chapter_level: int = 0,
) -> ExtractionPlan:
    """Build a dry-run plan of what *would* be extracted without writing files."""
    PdfReader, _ = _load_pypdf()
    reader = PdfReader(pdf_path)
    outline = getattr(reader, "outline", None)
    if not outline:
        raise ValueError("No bookmarks (outline) found in this PDF.")

    chapter_starts = find_chapter_bookmarks(outline, reader, chapter_level)
    if not chapter_starts:
        available_levels = get_available_bookmark_levels(outline)
        levels_text = (
            f" Available levels: {sorted(available_levels)}."
            if available_levels
            else " No page-linking bookmarks were found."
        )
        raise ValueError(f"No bookmarks found at level {chapter_level}.{levels_text}")

    chapter_starts.sort(key=lambda c: c.start_page)

    # Deduplicate by start page
    unique_starts: list[ChapterStart] = []
    seen_pages: set[int] = set()
    for ch in chapter_starts:
        if ch.start_page not in seen_pages:
            unique_starts.append(ch)
            seen_pages.add(ch.start_page)

    num_pages_total = len(reader.pages)
    plan = ExtractionPlan(total_pages=num_pages_total, level=chapter_level)

    used_filenames: set[str] = set()
    for idx, chapter in enumerate(unique_starts):
        start_page_idx = chapter.start_page
        end_page_idx = unique_starts[idx + 1].start_page - 1 if idx + 1 < len(unique_starts) else num_pages_total - 1

        if start_page_idx > end_page_idx:
            continue
        if not (0 <= start_page_idx < num_pages_total and 0 <= end_page_idx < num_pages_total):
            continue

        filename = f"{idx + 1:03d}_{sanitize_filename(chapter.title)}.pdf"
        # Avoid duplicate filenames
        if filename in used_filenames:
            base, ext = os.path.splitext(filename)
            counter = 2
            while f"{base}_{counter}{ext}" in used_filenames:
                counter += 1
            filename = f"{base}_{counter}{ext}"
        used_filenames.add(filename)

        plan.chapters.append({
            "idx": idx,
            "title": chapter.title,
            "start_page": start_page_idx + 1,
            "end_page": end_page_idx + 1,
            "pages_count": end_page_idx - start_page_idx + 1,
            "filename": filename,
        })

    return plan


# ---------------------------------------------------------------------------
#  Extraction – the main workhorse
# ---------------------------------------------------------------------------

def _write_chapter_fast(reader, start_idx: int, end_idx: int, output_path: str) -> None:
    """Write pages [start_idx, end_idx] to *output_path* using fast batch append."""
    _, PdfWriter = _load_pypdf()
    writer = PdfWriter()
    # append() with a page range is dramatically faster than per-page add_page()
    # because it batch-copies indirect objects instead of cloning each page.
    writer.append(reader, pages=list(range(start_idx, end_idx + 1)))
    with open(output_path, "wb") as f:
        writer.write(f)


def extract_chapters_from_pdf(
    pdf_path: str,
    output_dir: str = "chapters_output",
    chapter_level: int = 0,
    logger: Callable[[str], None] | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> dict:
    """Extract chapter PDFs based on bookmarks at *chapter_level*.

    Parameters
    ----------
    progress_callback : callable(current, total) -> None, optional
        Called after each chapter is written.
    cancel_event : threading.Event, optional
        If set, the extraction loop will abort early.
    """
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF file not found: '{pdf_path}'")
    if chapter_level < 0:
        raise ValueError("chapter_level must be >= 0")

    os.makedirs(output_dir, exist_ok=True)
    _log(f"Processing '{pdf_path}'...", logger)

    PdfReader, _ = _load_pypdf()
    reader = PdfReader(pdf_path)
    outline = getattr(reader, "outline", None)
    if not outline:
        raise ValueError("No bookmarks (outline) found in this PDF.")

    chapter_starts = find_chapter_bookmarks(outline, reader, chapter_level)
    if not chapter_starts:
        available_levels = get_available_bookmark_levels(outline)
        levels_text = (
            f" Available levels: {sorted(available_levels)}."
            if available_levels
            else " No page-linking bookmarks were found."
        )
        raise ValueError(f"No bookmarks found at level {chapter_level}.{levels_text}")

    chapter_starts.sort(key=lambda c: c.start_page)

    # Deduplicate by start page
    unique_starts: list[ChapterStart] = []
    seen_pages: set[int] = set()
    for chapter in chapter_starts:
        if chapter.start_page not in seen_pages:
            unique_starts.append(chapter)
            seen_pages.add(chapter.start_page)

    num_pages_total = len(reader.pages)
    _log(f"Found {len(unique_starts)} chapters at level {chapter_level}.", logger)

    saved_files: list[str] = []
    used_filenames: set[str] = set()

    for idx, chapter in enumerate(unique_starts):
        if cancel_event and cancel_event.is_set():
            _log("Extraction cancelled by user.", logger)
            break

        start_page_idx = chapter.start_page
        end_page_idx = unique_starts[idx + 1].start_page - 1 if idx + 1 < len(unique_starts) else num_pages_total - 1

        if start_page_idx > end_page_idx:
            _log(f"  Skipping '{chapter.title}' due to empty range ({start_page_idx + 1}-{end_page_idx + 1}).", logger)
            continue
        if not (0 <= start_page_idx < num_pages_total and 0 <= end_page_idx < num_pages_total):
            _log(f"  Skipping '{chapter.title}' due to invalid range ({start_page_idx + 1}-{end_page_idx + 1}).", logger)
            continue

        _log(f"  Extracting '{chapter.title}' (pages {start_page_idx + 1}-{end_page_idx + 1})", logger)

        # Build a unique output filename
        output_filename = f"{idx + 1:03d}_{sanitize_filename(chapter.title)}.pdf"
        if output_filename in used_filenames:
            base, ext = os.path.splitext(output_filename)
            counter = 2
            while f"{base}_{counter}{ext}" in used_filenames:
                counter += 1
            output_filename = f"{base}_{counter}{ext}"
        used_filenames.add(output_filename)

        output_pdf_path = os.path.join(output_dir, output_filename)
        output_pdf_path = _unique_path(output_pdf_path)

        _write_chapter_fast(reader, start_page_idx, end_page_idx, output_pdf_path)

        saved_files.append(output_pdf_path)
        _log(f"    Saved: {output_pdf_path}", logger)

        if progress_callback:
            progress_callback(idx + 1, len(unique_starts))

    _log("Extraction complete.", logger)
    return {
        "chapters_found": len(unique_starts),
        "files_written": saved_files,
        "output_dir": output_dir,
    }


def extract_selected_chapters(
    pdf_path: str,
    plan_entries: list[dict],
    output_dir: str = "chapters_output",
    logger: Callable[[str], None] | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> dict:
    """Extract only the chapters specified by *plan_entries* (from an ExtractionPlan).

    Each entry must have keys: title, start_page, end_page, filename (1-indexed pages).
    """
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF file not found: '{pdf_path}'")

    os.makedirs(output_dir, exist_ok=True)
    _log(f"Processing '{pdf_path}'...", logger)

    PdfReader, _ = _load_pypdf()
    reader = PdfReader(pdf_path)
    total = len(plan_entries)
    _log(f"Extracting {total} selected chapter(s).", logger)

    saved_files: list[str] = []

    for i, entry in enumerate(plan_entries):
        if cancel_event and cancel_event.is_set():
            _log("Extraction cancelled by user.", logger)
            break

        start_idx = entry["start_page"] - 1  # convert to 0-indexed
        end_idx = entry["end_page"] - 1
        title = entry["title"]
        filename = entry["filename"]

        _log(f"  Extracting '{title}' (pages {entry['start_page']}-{entry['end_page']})", logger)

        output_pdf_path = os.path.join(output_dir, filename)
        output_pdf_path = _unique_path(output_pdf_path)

        _write_chapter_fast(reader, start_idx, end_idx, output_pdf_path)

        saved_files.append(output_pdf_path)
        _log(f"    Saved: {output_pdf_path}", logger)

        if progress_callback:
            progress_callback(i + 1, total)

    _log("Extraction complete.", logger)
    return {
        "chapters_found": total,
        "files_written": saved_files,
        "output_dir": output_dir,
    }


# ---------------------------------------------------------------------------
#  Page-range extraction (no bookmarks needed)
# ---------------------------------------------------------------------------

def extract_page_range(
    pdf_path: str,
    start_page: int,
    end_page: int,
    output_path: str | None = None,
    logger: Callable[[str], None] | None = None,
) -> str:
    """Extract pages *start_page* to *end_page* (1-indexed, inclusive) into a new PDF.

    Returns the path of the written file.
    """
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF file not found: '{pdf_path}'")

    PdfReader, PdfWriter = _load_pypdf()
    reader = PdfReader(pdf_path)
    total = len(reader.pages)

    if start_page < 1 or end_page < 1 or start_page > total or end_page > total:
        raise ValueError(f"Page range {start_page}-{end_page} is out of bounds (PDF has {total} pages).")
    if start_page > end_page:
        raise ValueError(f"start_page ({start_page}) must be <= end_page ({end_page}).")

    if output_path is None:
        stem = Path(pdf_path).stem
        output_path = f"{stem}_pages_{start_page}-{end_page}.pdf"
    output_path = _unique_path(output_path)

    writer = PdfWriter()
    writer.append(reader, pages=list(range(start_page - 1, end_page)))

    with open(output_path, "wb") as f:
        writer.write(f)

    _log(f"Extracted pages {start_page}-{end_page} → {output_path}", logger)
    return output_path


# ---------------------------------------------------------------------------
#  Merge PDFs
# ---------------------------------------------------------------------------

def merge_pdfs(
    pdf_paths: list[str],
    output_path: str = "merged.pdf",
    logger: Callable[[str], None] | None = None,
) -> str:
    """Merge several PDFs into one, in the order given.

    Returns the path of the merged file.
    """
    if not pdf_paths:
        raise ValueError("No PDF files provided for merging.")

    PdfReader, PdfWriter = _load_pypdf()
    writer = PdfWriter()

    for p in pdf_paths:
        if not os.path.exists(p):
            raise FileNotFoundError(f"PDF file not found: '{p}'")
        reader = PdfReader(p)
        writer.append(reader)  # batch append is much faster than per-page add_page
        _log(f"  Added {len(reader.pages)} pages from '{os.path.basename(p)}'", logger)

    output_path = _unique_path(output_path)
    with open(output_path, "wb") as f:
        writer.write(f)

    _log(f"Merged {len(pdf_paths)} files → {output_path}", logger)
    return output_path


# ═══════════════════════════════════════════════════════════════════════════
#  GUI
# ═══════════════════════════════════════════════════════════════════════════

def run_gui() -> None:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    # ── colour palette ────────────────────────────────────────────────────
    BG          = "#1e1e2e"
    BG_DARKER   = "#181825"
    BG_LIGHTER  = "#313244"
    FG          = "#cdd6f4"
    FG_DIM      = "#a6adc8"
    ACCENT      = "#89b4fa"
    ACCENT_DARK = "#74c7ec"
    GREEN       = "#a6e3a1"
    RED         = "#f38ba8"
    YELLOW      = "#f9e2af"
    SURFACE     = "#45475a"
    BORDER      = "#585b70"

    class App:
        def __init__(self, root: tk.Tk) -> None:
            self.root = root
            self.root.title("PDF Chapter Extractor")
            self.root.geometry("960x720")
            self.root.minsize(760, 560)
            self.root.configure(bg=BG)

            # ── state ─────────────────────────────────────────────────────
            self.log_queue: queue.Queue[str] = queue.Queue()
            self.pdf_path_var = tk.StringVar()
            self.output_dir_var = tk.StringVar(value="chapters_output")
            self.level_var = tk.IntVar(value=0)
            self._extracting = False
            self._cancel_event = threading.Event()
            self._inspection_cache: dict | None = None
            self._current_plan: ExtractionPlan | None = None  # cached preview plan
            self._check_states: dict[str, bool] = {}  # iid -> checked

            # ── styling ──────────────────────────────────────────────────
            self._configure_styles()
            self._build_ui()
            self.root.after(100, self._drain_logs)

        # ─── ttk styling ─────────────────────────────────────────────────
        def _configure_styles(self) -> None:
            style = ttk.Style(self.root)
            style.theme_use("clam")

            style.configure(".", background=BG, foreground=FG, fieldbackground=BG_LIGHTER,
                            bordercolor=BORDER, troughcolor=BG_DARKER, focuscolor=ACCENT)
            style.configure("TFrame", background=BG)
            style.configure("TLabel", background=BG, foreground=FG, font=("Segoe UI", 10))
            style.configure("Title.TLabel", background=BG, foreground=ACCENT, font=("Segoe UI", 18, "bold"))
            style.configure("Subtitle.TLabel", background=BG, foreground=FG_DIM, font=("Segoe UI", 9))
            style.configure("Section.TLabel", background=BG, foreground=FG, font=("Segoe UI", 11, "bold"))
            style.configure("Status.TLabel", background=BG_DARKER, foreground=FG_DIM, font=("Segoe UI", 9))
            style.configure("Summary.TLabel", background=BG, foreground=YELLOW, font=("Segoe UI", 10, "bold"))

            style.configure("TEntry", fieldbackground=BG_LIGHTER, foreground=FG,
                            insertcolor=FG, bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER)
            style.map("TEntry", bordercolor=[("focus", ACCENT)])

            style.configure("TSpinbox", fieldbackground=BG_LIGHTER, foreground=FG,
                            arrowcolor=FG, bordercolor=BORDER)
            style.map("TSpinbox", bordercolor=[("focus", ACCENT)])

            style.configure("Accent.TButton", background=ACCENT, foreground=BG_DARKER,
                            font=("Segoe UI", 10, "bold"), padding=(12, 6))
            style.map("Accent.TButton",
                       background=[("active", ACCENT_DARK), ("disabled", SURFACE)],
                       foreground=[("disabled", FG_DIM)])

            style.configure("TButton", background=BG_LIGHTER, foreground=FG,
                            font=("Segoe UI", 10), padding=(10, 5), bordercolor=BORDER)
            style.map("TButton",
                       background=[("active", SURFACE)],
                       foreground=[("disabled", FG_DIM)])

            style.configure("Cancel.TButton", background=RED, foreground=BG_DARKER,
                            font=("Segoe UI", 10, "bold"), padding=(10, 5))
            style.map("Cancel.TButton", background=[("active", "#eba0ac")])

            style.configure("Green.TButton", background=GREEN, foreground=BG_DARKER,
                            font=("Segoe UI", 10, "bold"), padding=(10, 5))
            style.map("Green.TButton", background=[("active", "#94e2d5")])

            style.configure("TProgressbar", background=ACCENT, troughcolor=BG_LIGHTER,
                            bordercolor=BORDER, lightcolor=ACCENT, darkcolor=ACCENT)

            style.configure("TNotebook", background=BG, bordercolor=BORDER)
            style.configure("TNotebook.Tab", background=BG_LIGHTER, foreground=FG_DIM,
                            font=("Segoe UI", 10), padding=(14, 6))
            style.map("TNotebook.Tab",
                       background=[("selected", BG)],
                       foreground=[("selected", ACCENT)])

            style.configure("Treeview", background=BG_LIGHTER, foreground=FG,
                            fieldbackground=BG_LIGHTER, bordercolor=BORDER,
                            font=("Segoe UI", 9), rowheight=24)
            style.configure("Treeview.Heading", background=SURFACE, foreground=FG,
                            font=("Segoe UI", 9, "bold"))
            style.map("Treeview",
                       background=[("selected", ACCENT)],
                       foreground=[("selected", BG_DARKER)])

            style.configure("TLabelframe", background=BG, foreground=FG, bordercolor=BORDER)
            style.configure("TLabelframe.Label", background=BG, foreground=ACCENT, font=("Segoe UI", 10, "bold"))

            style.configure("TSeparator", background=BORDER)

        # ─── build the main UI ───────────────────────────────────────────
        def _build_ui(self) -> None:
            # header
            header = ttk.Frame(self.root)
            header.pack(fill="x", padx=20, pady=(16, 4))
            ttk.Label(header, text="PDF Chapter Extractor", style="Title.TLabel").pack(anchor="w")
            ttk.Label(header, text="Split bookmarked PDFs into individual chapter files", style="Subtitle.TLabel").pack(anchor="w")

            ttk.Separator(self.root).pack(fill="x", padx=20, pady=8)

            # ── notebook (tabs) ───────────────────────────────────────────
            self.notebook = ttk.Notebook(self.root)
            self.notebook.pack(fill="both", expand=True, padx=20, pady=(0, 8))

            self._build_extract_tab()
            self._build_page_range_tab()
            self._build_merge_tab()

            # ── status bar ────────────────────────────────────────────────
            status_bar = ttk.Frame(self.root, style="TFrame")
            status_bar.pack(fill="x", side="bottom")
            status_inner = tk.Frame(status_bar, bg=BG_DARKER, height=28)
            status_inner.pack(fill="x")
            self.status_label = tk.Label(status_inner, text="Ready", bg=BG_DARKER, fg=FG_DIM,
                                         font=("Segoe UI", 9), anchor="w", padx=16)
            self.status_label.pack(fill="x")

        # ═══════════════════════════════════════════════════════════════
        #  Tab 1: Chapter extraction
        # ═══════════════════════════════════════════════════════════════
        def _build_extract_tab(self) -> None:
            tab = ttk.Frame(self.notebook, padding=12)
            self.notebook.add(tab, text="📁 Chapter Extraction")

            # -- File selection --
            file_frame = ttk.LabelFrame(tab, text="  Input / Output  ", padding=10)
            file_frame.pack(fill="x", pady=(0, 8))

            pdf_row = ttk.Frame(file_frame)
            pdf_row.pack(fill="x", pady=3)
            ttk.Label(pdf_row, text="PDF file:", width=14, anchor="e").pack(side="left")
            ttk.Entry(pdf_row, textvariable=self.pdf_path_var).pack(side="left", fill="x", expand=True, padx=8)
            ttk.Button(pdf_row, text="Browse…", command=self._select_pdf).pack(side="left")

            out_row = ttk.Frame(file_frame)
            out_row.pack(fill="x", pady=3)
            ttk.Label(out_row, text="Output folder:", width=14, anchor="e").pack(side="left")
            ttk.Entry(out_row, textvariable=self.output_dir_var).pack(side="left", fill="x", expand=True, padx=8)
            ttk.Button(out_row, text="Browse…", command=self._select_output).pack(side="left")

            # -- Controls --
            ctrl_frame = ttk.Frame(tab)
            ctrl_frame.pack(fill="x", pady=6)

            left_ctrl = ttk.Frame(ctrl_frame)
            left_ctrl.pack(side="left")
            ttk.Label(left_ctrl, text="Chapter level:").pack(side="left")
            ttk.Spinbox(left_ctrl, from_=0, to=20, textvariable=self.level_var, width=5).pack(side="left", padx=6)

            right_ctrl = ttk.Frame(ctrl_frame)
            right_ctrl.pack(side="right")
            ttk.Button(right_ctrl, text="🔎 Inspect", command=self._inspect).pack(side="left", padx=4)
            ttk.Button(right_ctrl, text="📋 Preview", command=self._preview_extraction).pack(side="left", padx=4)
            self.extract_btn = ttk.Button(right_ctrl, text="⚡ Extract All", style="Accent.TButton", command=self._extract)
            self.extract_btn.pack(side="left", padx=4)
            self.extract_sel_btn = ttk.Button(right_ctrl, text="⚡ Extract Selected", style="Accent.TButton", command=self._extract_selected)
            self.extract_sel_btn.pack(side="left", padx=4)
            self.cancel_btn = ttk.Button(right_ctrl, text="✖ Cancel", style="Cancel.TButton", command=self._cancel_extraction)
            self.cancel_btn.pack(side="left", padx=4)
            self.cancel_btn.state(["disabled"])

            # -- Summary line --
            self.summary_label = ttk.Label(tab, text="Load a PDF and click Inspect to see bookmark levels.", style="Summary.TLabel")
            self.summary_label.pack(fill="x", pady=(4, 2))

            # -- Progress bar --
            self.progress_var = tk.DoubleVar(value=0)
            self.progress_bar = ttk.Progressbar(tab, variable=self.progress_var, maximum=100)
            self.progress_bar.pack(fill="x", pady=(0, 6))

            # -- Preview tree ──────────────────────────────────────────────
            preview_frame = ttk.LabelFrame(tab, text="  Extraction Preview  (click ☐ to toggle)", padding=6)
            preview_frame.pack(fill="both", expand=True, pady=(0, 6))

            # Selection buttons row
            sel_row = ttk.Frame(preview_frame)
            sel_row.pack(fill="x", pady=(0, 4))
            ttk.Button(sel_row, text="☑ Select All", command=self._select_all_chapters).pack(side="left", padx=2)
            ttk.Button(sel_row, text="☐ Deselect All", command=self._deselect_all_chapters).pack(side="left", padx=2)
            ttk.Button(sel_row, text="⇆ Invert", command=self._invert_chapter_selection).pack(side="left", padx=2)
            self._sel_count_label = ttk.Label(sel_row, text="", style="Subtitle.TLabel")
            self._sel_count_label.pack(side="right", padx=8)

            cols = ("check", "title", "pages", "count", "filename")
            self.preview_tree = ttk.Treeview(preview_frame, columns=cols, show="headings", height=6)
            self.preview_tree.heading("check", text="✓")
            self.preview_tree.heading("title", text="Chapter Title")
            self.preview_tree.heading("pages", text="Page Range")
            self.preview_tree.heading("count", text="Pages")
            self.preview_tree.heading("filename", text="Output File")
            self.preview_tree.column("check", width=36, anchor="center", stretch=False)
            self.preview_tree.column("title", width=260)
            self.preview_tree.column("pages", width=90, anchor="center")
            self.preview_tree.column("count", width=55, anchor="center")
            self.preview_tree.column("filename", width=250)

            # Click to toggle checkbox
            self.preview_tree.bind("<ButtonRelease-1>", self._on_tree_click)

            tree_scroll = ttk.Scrollbar(preview_frame, orient="vertical", command=self.preview_tree.yview)
            self.preview_tree.configure(yscrollcommand=tree_scroll.set)
            self.preview_tree.pack(side="left", fill="both", expand=True)
            tree_scroll.pack(side="right", fill="y")

            # -- Log panel --
            log_frame = ttk.LabelFrame(tab, text="  Log  ", padding=6)
            log_frame.pack(fill="both", expand=True)

            self.log_text = tk.Text(log_frame, wrap="word", height=8, bg=BG_DARKER, fg=FG,
                                     insertbackground=FG, font=("Consolas", 9), bd=0, relief="flat")
            self.log_text.pack(fill="both", expand=True)
            self.log_text.configure(state="disabled")

            # -- Bottom buttons --
            bottom = ttk.Frame(tab)
            bottom.pack(fill="x", pady=(4, 0))
            ttk.Button(bottom, text="📂 Open Output Folder", style="Green.TButton", command=self._open_output).pack(side="left")
            ttk.Button(bottom, text="🗑 Clear Log", command=self._clear_log).pack(side="right")

        # ═══════════════════════════════════════════════════════════════
        #  Tab 2: Page Range extraction
        # ═══════════════════════════════════════════════════════════════
        def _build_page_range_tab(self) -> None:
            tab = ttk.Frame(self.notebook, padding=12)
            self.notebook.add(tab, text="📄 Page Range")

            desc = ttk.Label(tab, text="Extract a specific range of pages from a PDF — no bookmarks required.",
                             style="Subtitle.TLabel")
            desc.pack(anchor="w", pady=(0, 10))

            # re-use main PDF path
            info_label = ttk.Label(tab, text="Uses the PDF file from the main tab.", foreground=FG_DIM)
            info_label.pack(anchor="w", pady=(0, 8))

            range_frame = ttk.LabelFrame(tab, text="  Page Range  ", padding=10)
            range_frame.pack(fill="x", pady=(0, 10))

            row = ttk.Frame(range_frame)
            row.pack(fill="x", pady=3)
            ttk.Label(row, text="Start page:", width=14, anchor="e").pack(side="left")
            self.range_start_var = tk.IntVar(value=1)
            ttk.Spinbox(row, from_=1, to=99999, textvariable=self.range_start_var, width=8).pack(side="left", padx=8)
            ttk.Label(row, text="End page:").pack(side="left", padx=(16, 0))
            self.range_end_var = tk.IntVar(value=1)
            ttk.Spinbox(row, from_=1, to=99999, textvariable=self.range_end_var, width=8).pack(side="left", padx=8)

            out_row = ttk.Frame(range_frame)
            out_row.pack(fill="x", pady=3)
            ttk.Label(out_row, text="Output file:", width=14, anchor="e").pack(side="left")
            self.range_output_var = tk.StringVar()
            ttk.Entry(out_row, textvariable=self.range_output_var).pack(side="left", fill="x", expand=True, padx=8)
            ttk.Label(out_row, text="(leave blank for auto-name)", foreground=FG_DIM).pack(side="left")

            ttk.Button(tab, text="⚡ Extract Page Range", style="Accent.TButton", command=self._extract_page_range).pack(pady=10)

            self.range_status = ttk.Label(tab, text="", style="Subtitle.TLabel")
            self.range_status.pack(anchor="w")

        # ═══════════════════════════════════════════════════════════════
        #  Tab 3: Merge PDFs
        # ═══════════════════════════════════════════════════════════════
        def _build_merge_tab(self) -> None:
            tab = ttk.Frame(self.notebook, padding=12)
            self.notebook.add(tab, text="🔗 Merge PDFs")

            desc = ttk.Label(tab, text="Combine multiple PDF files into a single document.",
                             style="Subtitle.TLabel")
            desc.pack(anchor="w", pady=(0, 10))

            list_frame = ttk.LabelFrame(tab, text="  Files to Merge (in order)  ", padding=10)
            list_frame.pack(fill="both", expand=True, pady=(0, 8))

            self.merge_listbox = tk.Listbox(list_frame, bg=BG_LIGHTER, fg=FG, selectbackground=ACCENT,
                                             selectforeground=BG_DARKER, font=("Segoe UI", 9), bd=0,
                                             relief="flat", height=10)
            merge_scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.merge_listbox.yview)
            self.merge_listbox.configure(yscrollcommand=merge_scroll.set)
            self.merge_listbox.pack(side="left", fill="both", expand=True)
            merge_scroll.pack(side="right", fill="y")

            self._merge_files: list[str] = []

            btn_row = ttk.Frame(tab)
            btn_row.pack(fill="x", pady=4)
            ttk.Button(btn_row, text="➕ Add Files…", command=self._merge_add_files).pack(side="left", padx=4)
            ttk.Button(btn_row, text="🔼 Move Up", command=self._merge_move_up).pack(side="left", padx=4)
            ttk.Button(btn_row, text="🔽 Move Down", command=self._merge_move_down).pack(side="left", padx=4)
            ttk.Button(btn_row, text="❌ Remove", command=self._merge_remove).pack(side="left", padx=4)
            ttk.Button(btn_row, text="🗑 Clear All", command=self._merge_clear).pack(side="left", padx=4)

            out_row = ttk.Frame(tab)
            out_row.pack(fill="x", pady=4)
            ttk.Label(out_row, text="Output file:", width=14, anchor="e").pack(side="left")
            self.merge_output_var = tk.StringVar(value="merged.pdf")
            ttk.Entry(out_row, textvariable=self.merge_output_var).pack(side="left", fill="x", expand=True, padx=8)
            ttk.Button(out_row, text="Browse…", command=self._merge_select_output).pack(side="left")

            ttk.Button(tab, text="⚡ Merge PDFs", style="Accent.TButton", command=self._do_merge).pack(pady=10)
            self.merge_status = ttk.Label(tab, text="", style="Subtitle.TLabel")
            self.merge_status.pack(anchor="w")

        # ─── Logging helpers ──────────────────────────────────────────
        def _append_log(self, msg: str) -> None:
            self.log_text.configure(state="normal")
            self.log_text.insert("end", f"{msg}\n")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")

        def _drain_logs(self) -> None:
            while not self.log_queue.empty():
                self._append_log(self.log_queue.get_nowait())
            self.root.after(100, self._drain_logs)

        def _clear_log(self) -> None:
            self.log_text.configure(state="normal")
            self.log_text.delete("1.0", "end")
            self.log_text.configure(state="disabled")

        def _set_status(self, text: str) -> None:
            self.status_label.configure(text=text)

        # ─── Validation ──────────────────────────────────────────────
        def _validate_pdf(self) -> str | None:
            path = self.pdf_path_var.get().strip()
            if not path:
                messagebox.showerror("Missing PDF", "Please choose a PDF file first.")
                return None
            if not os.path.exists(path):
                messagebox.showerror("Invalid path", "The selected PDF file does not exist.")
                return None
            return path

        # ─── File dialogs ────────────────────────────────────────────
        def _select_pdf(self) -> None:
            selected = filedialog.askopenfilename(
                title="Select PDF", filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")]
            )
            if selected:
                self.pdf_path_var.set(selected)
                self._inspection_cache = None
                self.summary_label.config(text="PDF changed — click Inspect to reload bookmark info.")

        def _select_output(self) -> None:
            selected = filedialog.askdirectory(title="Select output directory")
            if selected:
                self.output_dir_var.set(selected)

        # ─── Inspect ─────────────────────────────────────────────────
        def _inspect(self) -> None:
            path = self._validate_pdf()
            if not path:
                return

            self._set_status("Inspecting PDF…")

            def worker():
                try:
                    info = inspect_pdf(path)
                    self._inspection_cache = info
                    summary = f"Pages: {info['pages']} | Bookmarks: {'yes' if info['has_outline'] else 'no'}"
                    if info["levels"]:
                        summary += f" | Levels: {info['levels']}"
                    self.root.after(0, lambda: self.summary_label.config(text=summary))
                    self.log_queue.put(summary)
                    self.root.after(0, lambda: self._set_status("Inspection complete"))
                except Exception as exc:
                    self.root.after(0, lambda: messagebox.showerror("Inspect failed", str(exc)))
                    self.root.after(0, lambda: self._set_status("Inspection failed"))

            threading.Thread(target=worker, daemon=True).start()

        # ─── Preview ─────────────────────────────────────────────────
        def _preview_extraction(self) -> None:
            path = self._validate_pdf()
            if not path:
                return
            chapter_level = int(self.level_var.get())

            self._set_status("Building extraction preview…")

            def worker():
                try:
                    plan = build_extraction_plan(path, chapter_level)
                    self.root.after(0, lambda: self._show_plan(plan))
                    self.root.after(0, lambda: self._set_status(f"Preview: {len(plan.chapters)} chapters"))
                except Exception as exc:
                    self.root.after(0, lambda: messagebox.showerror("Preview failed", str(exc)))
                    self.root.after(0, lambda: self._set_status("Preview failed"))

            threading.Thread(target=worker, daemon=True).start()

        def _show_plan(self, plan: ExtractionPlan) -> None:
            self._current_plan = plan
            self._check_states.clear()
            for item in self.preview_tree.get_children():
                self.preview_tree.delete(item)
            for i, ch in enumerate(plan.chapters):
                iid = self.preview_tree.insert("", "end", values=(
                    "☑",
                    ch["title"],
                    f"{ch['start_page']}–{ch['end_page']}",
                    ch["pages_count"],
                    ch["filename"],
                ))
                self._check_states[iid] = True  # all checked by default
            self._update_sel_count()

        # ─── Checkbox helpers ─────────────────────────────────────────
        def _on_tree_click(self, event) -> None:
            """Toggle the checkbox when the user clicks on the check column."""
            region = self.preview_tree.identify_region(event.x, event.y)
            if region != "cell":
                return
            col = self.preview_tree.identify_column(event.x)
            if col != "#1":  # the "check" column
                return
            iid = self.preview_tree.identify_row(event.y)
            if not iid:
                return
            # Toggle
            checked = not self._check_states.get(iid, True)
            self._check_states[iid] = checked
            vals = list(self.preview_tree.item(iid, "values"))
            vals[0] = "☑" if checked else "☐"
            self.preview_tree.item(iid, values=vals)
            self._update_sel_count()

        def _select_all_chapters(self) -> None:
            for iid in self.preview_tree.get_children():
                self._check_states[iid] = True
                vals = list(self.preview_tree.item(iid, "values"))
                vals[0] = "☑"
                self.preview_tree.item(iid, values=vals)
            self._update_sel_count()

        def _deselect_all_chapters(self) -> None:
            for iid in self.preview_tree.get_children():
                self._check_states[iid] = False
                vals = list(self.preview_tree.item(iid, "values"))
                vals[0] = "☐"
                self.preview_tree.item(iid, values=vals)
            self._update_sel_count()

        def _invert_chapter_selection(self) -> None:
            for iid in self.preview_tree.get_children():
                checked = not self._check_states.get(iid, True)
                self._check_states[iid] = checked
                vals = list(self.preview_tree.item(iid, "values"))
                vals[0] = "☑" if checked else "☐"
                self.preview_tree.item(iid, values=vals)
            self._update_sel_count()

        def _update_sel_count(self) -> None:
            total = len(self._check_states)
            sel = sum(1 for v in self._check_states.values() if v)
            self._sel_count_label.config(text=f"{sel} / {total} selected")

        def _get_selected_plan_entries(self) -> list[dict]:
            """Return the plan entries that are currently checked in the preview tree."""
            if not self._current_plan:
                return []
            children = self.preview_tree.get_children()
            selected: list[dict] = []
            for iid, ch in zip(children, self._current_plan.chapters):
                if self._check_states.get(iid, True):
                    selected.append(ch)
            return selected

        # ─── Extract ─────────────────────────────────────────────────
        def _start_extraction_ui(self) -> None:
            """Shared UI state changes when extraction begins."""
            self._extracting = True
            self._cancel_event.clear()
            self.extract_btn.state(["disabled"])
            self.extract_sel_btn.state(["disabled"])
            self.cancel_btn.state(["!disabled"])
            self.progress_var.set(0)

        def _on_progress(self, current, total):
            pct = (current / total) * 100 if total else 0
            self.root.after(0, lambda: self.progress_var.set(pct))
            self.root.after(0, lambda: self._set_status(f"Extracting… {current}/{total}"))

        def _extract(self) -> None:
            """Extract ALL chapters (ignores checkbox selection)."""
            if self._extracting:
                return
            path = self._validate_pdf()
            if not path:
                return

            output_dir = self.output_dir_var.get().strip() or "chapters_output"
            chapter_level = int(self.level_var.get())

            self._start_extraction_ui()
            self._set_status("Extracting all chapters…")

            def worker():
                self.log_queue.put("Starting full extraction…")
                try:
                    result = extract_chapters_from_pdf(
                        path,
                        output_dir=output_dir,
                        chapter_level=chapter_level,
                        logger=self.log_queue.put,
                        progress_callback=lambda c, t: self._on_progress(c, t),
                        cancel_event=self._cancel_event,
                    )
                    n = len(result["files_written"])
                    self.log_queue.put(f"Done. Wrote {n} files to {result['output_dir']}")
                    self.root.after(0, lambda: self._set_status(f"Done — {n} files written"))
                    self.root.after(0, lambda: self.progress_var.set(100))
                except Exception as exc:
                    self.log_queue.put(f"Error: {exc}")
                    self.root.after(0, lambda: self._set_status("Extraction failed"))
                finally:
                    self.root.after(0, self._extraction_finished)

            threading.Thread(target=worker, daemon=True).start()

        def _extract_selected(self) -> None:
            """Extract only the chapters checked in the preview tree."""
            if self._extracting:
                return
            path = self._validate_pdf()
            if not path:
                return

            entries = self._get_selected_plan_entries()
            if not entries:
                messagebox.showinfo("Nothing selected",
                                    "No chapters are selected.\n\nClick Preview first, then check the chapters you want.")
                return

            output_dir = self.output_dir_var.get().strip() or "chapters_output"

            self._start_extraction_ui()
            self._set_status(f"Extracting {len(entries)} selected chapter(s)…")

            def worker():
                self.log_queue.put(f"Starting extraction of {len(entries)} selected chapter(s)…")
                try:
                    result = extract_selected_chapters(
                        path,
                        plan_entries=entries,
                        output_dir=output_dir,
                        logger=self.log_queue.put,
                        progress_callback=lambda c, t: self._on_progress(c, t),
                        cancel_event=self._cancel_event,
                    )
                    n = len(result["files_written"])
                    self.log_queue.put(f"Done. Wrote {n} files to {result['output_dir']}")
                    self.root.after(0, lambda: self._set_status(f"Done — {n} files written"))
                    self.root.after(0, lambda: self.progress_var.set(100))
                except Exception as exc:
                    self.log_queue.put(f"Error: {exc}")
                    self.root.after(0, lambda: self._set_status("Extraction failed"))
                finally:
                    self.root.after(0, self._extraction_finished)

            threading.Thread(target=worker, daemon=True).start()

        def _extraction_finished(self) -> None:
            self._extracting = False
            self.extract_btn.state(["!disabled"])
            self.extract_sel_btn.state(["!disabled"])
            self.cancel_btn.state(["disabled"])

        def _cancel_extraction(self) -> None:
            self._cancel_event.set()
            self.cancel_btn.state(["disabled"])
            self._set_status("Cancelling…")

        def _open_output(self) -> None:
            folder = self.output_dir_var.get().strip() or "chapters_output"
            if os.path.isdir(folder):
                open_folder(folder)
            else:
                messagebox.showinfo("Not found", f"Output folder does not exist yet:\n{folder}")

        # ─────────────────────────────────────────────────────────────
        #  Tab 2 actions — Page Range
        # ─────────────────────────────────────────────────────────────
        def _extract_page_range(self) -> None:
            path = self._validate_pdf()
            if not path:
                return
            start = self.range_start_var.get()
            end = self.range_end_var.get()
            out = self.range_output_var.get().strip() or None

            self._set_status("Extracting page range…")

            def worker():
                try:
                    result_path = extract_page_range(path, start, end, output_path=out)
                    msg = f"✓ Saved: {result_path}"
                    self.root.after(0, lambda: self.range_status.config(text=msg))
                    self.root.after(0, lambda: self._set_status("Page range extracted"))
                except Exception as exc:
                    self.root.after(0, lambda: messagebox.showerror("Extraction failed", str(exc)))
                    self.root.after(0, lambda: self._set_status("Page range extraction failed"))

            threading.Thread(target=worker, daemon=True).start()

        # ─────────────────────────────────────────────────────────────
        #  Tab 3 actions — Merge
        # ─────────────────────────────────────────────────────────────
        def _merge_refresh_listbox(self) -> None:
            self.merge_listbox.delete(0, "end")
            for i, p in enumerate(self._merge_files, 1):
                self.merge_listbox.insert("end", f"  {i}. {os.path.basename(p)}")

        def _merge_add_files(self) -> None:
            selected = filedialog.askopenfilenames(
                title="Select PDFs to merge", filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")]
            )
            if selected:
                self._merge_files.extend(selected)
                self._merge_refresh_listbox()

        def _merge_move_up(self) -> None:
            sel = self.merge_listbox.curselection()
            if sel and sel[0] > 0:
                idx = sel[0]
                self._merge_files[idx - 1], self._merge_files[idx] = self._merge_files[idx], self._merge_files[idx - 1]
                self._merge_refresh_listbox()
                self.merge_listbox.selection_set(idx - 1)

        def _merge_move_down(self) -> None:
            sel = self.merge_listbox.curselection()
            if sel and sel[0] < len(self._merge_files) - 1:
                idx = sel[0]
                self._merge_files[idx + 1], self._merge_files[idx] = self._merge_files[idx], self._merge_files[idx + 1]
                self._merge_refresh_listbox()
                self.merge_listbox.selection_set(idx + 1)

        def _merge_remove(self) -> None:
            sel = self.merge_listbox.curselection()
            if sel:
                del self._merge_files[sel[0]]
                self._merge_refresh_listbox()

        def _merge_clear(self) -> None:
            self._merge_files.clear()
            self._merge_refresh_listbox()

        def _merge_select_output(self) -> None:
            selected = filedialog.asksaveasfilename(
                title="Save merged PDF as",
                defaultextension=".pdf",
                filetypes=[("PDF files", "*.pdf")],
            )
            if selected:
                self.merge_output_var.set(selected)

        def _do_merge(self) -> None:
            if not self._merge_files:
                messagebox.showinfo("No files", "Add at least one PDF to merge.")
                return
            out = self.merge_output_var.get().strip() or "merged.pdf"
            files = list(self._merge_files)
            self._set_status("Merging PDFs…")

            def worker():
                try:
                    result_path = merge_pdfs(files, output_path=out)
                    msg = f"✓ Merged {len(files)} files → {result_path}"
                    self.root.after(0, lambda: self.merge_status.config(text=msg))
                    self.root.after(0, lambda: self._set_status("Merge complete"))
                except Exception as exc:
                    self.root.after(0, lambda: messagebox.showerror("Merge failed", str(exc)))
                    self.root.after(0, lambda: self._set_status("Merge failed"))

            threading.Thread(target=worker, daemon=True).start()

    root = tk.Tk()
    App(root)
    root.mainloop()


# ═══════════════════════════════════════════════════════════════════════════
#  CLI entry point
# ═══════════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract chapter PDFs from bookmarks, with CLI or Tkinter GUI."
    )
    parser.add_argument("pdf_path", nargs="?", help="Path to the input PDF file.")
    parser.add_argument(
        "-o", "--output_dir",
        default="chapters_output",
        help="Directory to save extracted chapter PDFs.",
    )
    parser.add_argument(
        "-l", "--chapter_level",
        type=int,
        default=0,
        help="Bookmark depth to treat as chapter starts (0 = top-level).",
    )
    parser.add_argument(
        "--inspect-only",
        action="store_true",
        help="Show bookmark-level metadata without extracting.",
    )
    parser.add_argument(
        "--gui",
        action="store_true",
        help="Launch a desktop GUI for selecting files and extracting chapters.",
    )
    parser.add_argument(
        "--page-range",
        metavar="START-END",
        help="Extract a page range (e.g. 10-25). 1-indexed, inclusive.",
    )
    parser.add_argument(
        "--merge",
        nargs="+",
        metavar="PDF",
        help="Merge multiple PDFs into one (in order supplied).",
    )

    args = parser.parse_args()

    # GUI mode
    if args.gui:
        run_gui()
        return

    # Merge mode
    if args.merge:
        merge_pdfs(args.merge, output_path=args.output_dir if args.output_dir != "chapters_output" else "merged.pdf")
        return

    # Everything else needs a pdf_path
    if not args.pdf_path:
        parser.error("pdf_path is required unless --gui or --merge is used")

    pdf_path = str(Path(args.pdf_path))

    # Page range mode
    if args.page_range:
        try:
            parts = args.page_range.split("-")
            start, end = int(parts[0]), int(parts[1])
        except (ValueError, IndexError):
            parser.error("--page-range must be START-END, e.g. 10-25")
        extract_page_range(pdf_path, start, end)
        return

    # Inspect-only mode
    if args.inspect_only:
        info = inspect_pdf(pdf_path)
        print(f"Pages: {info['pages']}")
        print(f"Has outline: {info['has_outline']}")
        print(f"Available levels: {info['levels']}")
        if info["preview"]:
            print("\nBookmark tree:")
            for entry in info["preview"]:
                indent = "  " * entry["level"]
                pg = f" (p.{entry['page'] + 1})" if entry["page"] is not None else ""
                print(f"  {indent}• {entry['title']}{pg}")
        return

    # Default: extract chapters
    try:
        extract_chapters_from_pdf(
            pdf_path,
            output_dir=args.output_dir,
            chapter_level=args.chapter_level,
        )
    except Exception as exc:
        raise SystemExit(f"Error: {exc}")


if __name__ == "__main__":
    main()
