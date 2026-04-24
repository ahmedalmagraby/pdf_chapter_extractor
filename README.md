# PDF Chapter Extractor

Extract chapter PDFs from a bookmarked source PDF, extract specific page ranges, or merge multiple PDFs — all from a polished desktop GUI or the command line.

## Features

- **Chapter Extraction** – split a bookmarked PDF by outline level (`0`, `1`, `2`, …).
- **Selective Extraction** – preview chapters, then check/uncheck individual ones to extract only what you need.
- **Extraction Preview** – see exactly what will be extracted *before* writing files, with Select All / Deselect All / Invert controls.
- **Page Range Extraction** – pull out arbitrary page ranges without needing bookmarks.
- **PDF Merging** – combine multiple PDFs into one, with reorder support.
- **Fast Performance** – uses pypdf's batch `append()` API instead of per-page cloning for dramatically faster extraction.
- **Progress & Cancellation** – real-time progress bar with elapsed time and the ability to cancel mid-extraction.
- **Auto-Inspect** – selecting a PDF automatically loads its bookmark metadata.
- **Inspect mode** – view available bookmark levels and a bookmark tree.
- **Keyboard shortcuts** – `Ctrl+O` to open, `Ctrl+E` to extract, `Escape` to cancel.
- **Export Log** – save the extraction log to a text file for debugging.
- **Open Output Folder** – one-click to open the output directory in your file manager.
- **Remembers directories** – file dialogs start from the last-used folder.
- **Cross-platform** – works on Windows, macOS, and Linux.
- **Safe filenames** – output filenames are sanitized and deduplicated automatically.
- **Modern dark GUI** – a polished Catppuccin-themed Tkinter interface.

## Install

```bash
pip install pypdf
```

Python 3.10+ is required.

## CLI Usage

```bash
# Extract chapters at the default top-level bookmark depth
python pdf_chapter_extractor.py input.pdf

# Extract at bookmark level 1, custom output folder
python pdf_chapter_extractor.py input.pdf -o chapters -l 1

# Inspect bookmark metadata without extracting
python pdf_chapter_extractor.py input.pdf --inspect-only

# Extract a specific page range (1-indexed, inclusive)
python pdf_chapter_extractor.py input.pdf --page-range 10-25

# Merge multiple PDFs
python pdf_chapter_extractor.py --merge file1.pdf file2.pdf file3.pdf -o combined.pdf

# Launch the desktop GUI
python pdf_chapter_extractor.py --gui
```

## GUI Usage

Launch the app:

```bash
python pdf_chapter_extractor.py --gui
```

The GUI has three tabs:

### 📁 Chapter Extraction
1. Select a PDF file (`Ctrl+O`). The app **auto-inspects** and shows bookmark levels.
2. Choose an output folder.
3. Set the chapter level and click **Preview** to review what will be extracted.
4. Click anywhere on a row to **toggle its checkbox**. Use **Select All**, **Deselect All**, or **Invert** for bulk changes.
5. Click **Extract All** (`Ctrl+E`) to extract every chapter, or **Extract Selected** to extract only the checked ones.
6. A progress bar shows real-time status with **elapsed time**; press `Escape` or click **Cancel** to abort.
7. Click **Open Output Folder** to view the results, or **Export Log** to save the log.

### 📄 Page Range
1. After loading a PDF, the total page count is displayed so you know the valid range.
2. Set the start and end page numbers and click **Extract Page Range**.
3. Output is saved to the configured output folder by default.

### 🔗 Merge PDFs
1. Add PDF files and reorder them as needed.
2. Set the output filename.
3. Click **Merge PDFs**.

## Keyboard Shortcuts

| Shortcut    | Action              |
|-------------|---------------------|
| `Ctrl+O`    | Open PDF file       |
| `Ctrl+E`    | Extract all chapters|
| `Escape`    | Cancel extraction   |

## Notes

- The chapter extractor relies on PDF bookmarks/outlines. PDFs without bookmarks cannot be chapter-split (use the Page Range tab instead).
- If no bookmarks are found at a chosen level, use **Inspect** or `--inspect-only` to pick a valid level.
- Duplicate filenames are handled automatically with numeric suffixes.
