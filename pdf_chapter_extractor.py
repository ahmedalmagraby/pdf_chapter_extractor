import argparse
import os
import queue
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable



@dataclass
class ChapterStart:
    title: str
    start_page: int
    level: int




def _load_pypdf():
    try:
        from pypdf import PdfReader, PdfWriter
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "Missing dependency: pypdf. Install it with `pip install pypdf`."
        ) from exc
    return PdfReader, PdfWriter


def sanitize_filename(name: str) -> str:
    """Return a filesystem-safe chapter filename stem."""
    if not name:
        return "untitled"

    safe_name = re.sub(r'[<>:"/\\|?*]', "_", name)
    safe_name = re.sub(r"\s+", " ", safe_name).strip()
    safe_name = safe_name[:150]

    return safe_name or "untitled_chapter"


def _log(message: str, logger: Callable[[str], None] | None = None) -> None:
    if logger:
        logger(message)
    else:
        print(message)


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
        except Exception as exc:  # keep extraction resilient to malformed entries
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


def inspect_pdf(pdf_path: str) -> dict:
    """Inspect a PDF and return bookmark metadata useful for UI and CLI."""
    PdfReader, _ = _load_pypdf()
    reader = PdfReader(pdf_path)
    outline = getattr(reader, "outline", None)
    has_outline = bool(outline)
    levels = sorted(get_available_bookmark_levels(outline)) if has_outline else []

    return {
        "pages": len(reader.pages),
        "has_outline": has_outline,
        "levels": levels,
    }


def extract_chapters_from_pdf(
    pdf_path: str,
    output_dir: str = "chapters_output",
    chapter_level: int = 0,
    logger: Callable[[str], None] | None = None,
) -> dict:
    """Extract chapter PDFs based on bookmarks at `chapter_level`."""
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF file not found: '{pdf_path}'")

    if chapter_level < 0:
        raise ValueError("chapter_level must be >= 0")

    os.makedirs(output_dir, exist_ok=True)
    _log(f"Processing '{pdf_path}'...", logger)

    PdfReader, PdfWriter = _load_pypdf()
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

    chapter_starts.sort(key=lambda chapter: chapter.start_page)

    unique_starts: list[ChapterStart] = []
    seen_pages: set[int] = set()
    for chapter in chapter_starts:
        if chapter.start_page not in seen_pages:
            unique_starts.append(chapter)
            seen_pages.add(chapter.start_page)

    num_pages_total = len(reader.pages)
    _log(f"Found {len(unique_starts)} chapters at level {chapter_level}.", logger)

    saved_files: list[str] = []

    for idx, chapter in enumerate(unique_starts):
        start_page_idx = chapter.start_page
        if idx + 1 < len(unique_starts):
            end_page_idx = unique_starts[idx + 1].start_page - 1
        else:
            end_page_idx = num_pages_total - 1

        if start_page_idx > end_page_idx:
            _log(
                f"  Skipping '{chapter.title}' due to empty range "
                f"({start_page_idx + 1}-{end_page_idx + 1}).",
                logger,
            )
            continue

        if not (0 <= start_page_idx < num_pages_total and 0 <= end_page_idx < num_pages_total):
            _log(
                f"  Skipping '{chapter.title}' due to invalid range "
                f"({start_page_idx + 1}-{end_page_idx + 1}).",
                logger,
            )
            continue

        _log(
            f"  Extracting '{chapter.title}' (pages {start_page_idx + 1}-{end_page_idx + 1})",
            logger,
        )

        writer = PdfWriter()
        for page_num in range(start_page_idx, end_page_idx + 1):
            writer.add_page(reader.pages[page_num])

        if not writer.pages:
            _log(f"    Skipping '{chapter.title}' because no pages were collected.", logger)
            continue

        output_filename = f"{idx + 1:03d}_{sanitize_filename(chapter.title)}.pdf"
        output_pdf_path = os.path.join(output_dir, output_filename)

        with open(output_pdf_path, "wb") as file_out:
            writer.write(file_out)

        saved_files.append(output_pdf_path)
        _log(f"    Saved: {output_pdf_path}", logger)

    _log("Extraction complete.", logger)
    return {
        "chapters_found": len(unique_starts),
        "files_written": saved_files,
        "output_dir": output_dir,
    }


def run_gui() -> None:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    class App:
        def __init__(self, root: tk.Tk) -> None:
            self.root = root
            self.root.title("PDF Chapter Extractor")
            self.root.geometry("880x600")

            self.log_queue: queue.Queue[str] = queue.Queue()

            self.pdf_path_var = tk.StringVar()
            self.output_dir_var = tk.StringVar(value="chapters_output")
            self.level_var = tk.IntVar(value=0)

            self._build_ui(ttk)
            self.root.after(100, self._drain_logs)

        def _build_ui(self, ttk_mod) -> None:
            container = ttk_mod.Frame(self.root, padding=12)
            container.pack(fill="both", expand=True)

            pdf_row = ttk_mod.Frame(container)
            pdf_row.pack(fill="x", pady=4)
            ttk_mod.Label(pdf_row, text="PDF file:").pack(side="left")
            ttk_mod.Entry(pdf_row, textvariable=self.pdf_path_var).pack(
                side="left", fill="x", expand=True, padx=8
            )
            ttk_mod.Button(pdf_row, text="Browse", command=self.select_pdf).pack(side="left")

            out_row = ttk_mod.Frame(container)
            out_row.pack(fill="x", pady=4)
            ttk_mod.Label(out_row, text="Output folder:").pack(side="left")
            ttk_mod.Entry(out_row, textvariable=self.output_dir_var).pack(
                side="left", fill="x", expand=True, padx=8
            )
            ttk_mod.Button(out_row, text="Browse", command=self.select_output).pack(side="left")

            controls = ttk_mod.Frame(container)
            controls.pack(fill="x", pady=8)
            ttk_mod.Label(controls, text="Chapter level:").pack(side="left")
            ttk_mod.Spinbox(controls, from_=0, to=20, textvariable=self.level_var, width=5).pack(
                side="left", padx=6
            )

            ttk_mod.Button(controls, text="Inspect PDF", command=self.inspect).pack(side="left", padx=6)
            ttk_mod.Button(controls, text="Extract Chapters", command=self.extract).pack(
                side="left", padx=6
            )

            self.summary_label = ttk_mod.Label(container, text="Load a PDF to inspect bookmark levels.")
            self.summary_label.pack(fill="x", pady=(0, 8))

            self.log_text = tk.Text(container, wrap="word", height=25)
            self.log_text.pack(fill="both", expand=True)
            self.log_text.configure(state="disabled")

        def _append_log(self, msg: str) -> None:
            self.log_text.configure(state="normal")
            self.log_text.insert("end", f"{msg}\n")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")

        def _drain_logs(self) -> None:
            while not self.log_queue.empty():
                self._append_log(self.log_queue.get_nowait())
            self.root.after(100, self._drain_logs)

        def _validate_pdf(self) -> str | None:
            path = self.pdf_path_var.get().strip()
            if not path:
                messagebox.showerror("Missing PDF", "Please choose a PDF file first.")
                return None
            if not os.path.exists(path):
                messagebox.showerror("Invalid path", "The selected PDF file does not exist.")
                return None
            return path

        def select_pdf(self) -> None:
            selected = filedialog.askopenfilename(
                title="Select PDF", filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")]
            )
            if selected:
                self.pdf_path_var.set(selected)

        def select_output(self) -> None:
            selected = filedialog.askdirectory(title="Select output directory")
            if selected:
                self.output_dir_var.set(selected)

        def inspect(self) -> None:
            path = self._validate_pdf()
            if not path:
                return

            try:
                info = inspect_pdf(path)
                summary = (
                    f"Pages: {info['pages']} | Bookmarks: {'yes' if info['has_outline'] else 'no'}"
                )
                if info["levels"]:
                    summary += f" | Bookmark levels: {info['levels']}"
                self.summary_label.config(text=summary)
                self.log_queue.put(summary)
            except Exception as exc:
                messagebox.showerror("Inspect failed", str(exc))

        def extract(self) -> None:
            path = self._validate_pdf()
            if not path:
                return

            output_dir = self.output_dir_var.get().strip() or "chapters_output"
            chapter_level = int(self.level_var.get())

            def worker() -> None:
                self.log_queue.put("Starting extraction...")
                try:
                    result = extract_chapters_from_pdf(
                        path,
                        output_dir=output_dir,
                        chapter_level=chapter_level,
                        logger=self.log_queue.put,
                    )
                    self.log_queue.put(
                        f"Done. Wrote {len(result['files_written'])} files to {result['output_dir']}"
                    )
                except Exception as exc:
                    self.log_queue.put(f"Error: {exc}")

            threading.Thread(target=worker, daemon=True).start()

    root = tk.Tk()
    App(root)
    root.mainloop()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract chapter PDFs from bookmarks, with CLI or Tkinter GUI."
    )
    parser.add_argument("pdf_path", nargs="?", help="Path to the input PDF file.")
    parser.add_argument(
        "-o",
        "--output_dir",
        default="chapters_output",
        help="Directory to save extracted chapter PDFs.",
    )
    parser.add_argument(
        "-l",
        "--chapter_level",
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

    args = parser.parse_args()

    if args.gui:
        run_gui()
        return

    if not args.pdf_path:
        parser.error("pdf_path is required unless --gui is used")

    pdf_path = str(Path(args.pdf_path))

    if args.inspect_only:
        info = inspect_pdf(pdf_path)
        print(f"Pages: {info['pages']}")
        print(f"Has outline: {info['has_outline']}")
        print(f"Available levels: {info['levels']}")
        return

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
