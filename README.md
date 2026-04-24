# PDF Chapter Extractor

Extract chapter PDFs from a bookmarked source PDF.

## Features

- Extract chapters from bookmark levels (`0`, `1`, `2`, ...).
- Safe output filenames based on bookmark titles.
- `--inspect-only` mode to view available bookmark levels before extraction.
- Desktop GUI (`tkinter`) for users who prefer point-and-click workflows.

## Install

```bash
pip install pypdf
```

## CLI usage

```bash
python pdf_chapter_extractor.py input.pdf
```

Useful options:

```bash
python pdf_chapter_extractor.py input.pdf -o chapters -l 1
python pdf_chapter_extractor.py input.pdf --inspect-only
python pdf_chapter_extractor.py --gui
```

## GUI usage

Launch the app:

```bash
python pdf_chapter_extractor.py --gui
```

Then:

1. Select a PDF file.
2. Choose an output folder.
3. Click **Inspect PDF** to see available bookmark levels.
4. Set the chapter level and click **Extract Chapters**.
5. Monitor progress in the log panel.

## Notes

- The extractor relies on PDF bookmarks/outlines. PDFs without bookmarks cannot be chapter-split automatically.
- If no bookmarks are found at a chosen level, use `--inspect-only` or **Inspect PDF** in GUI to pick a valid level.
