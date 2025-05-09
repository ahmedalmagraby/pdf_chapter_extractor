# pdf_chapter_extractor

**How it works:**

1.  **`sanitize_filename(name)`:** A helper function to create valid filenames from chapter titles (which might contain special characters).
2.  **`find_chapter_bookmarks(outline_items, reader, target_level, current_level=0)`:**
    *   This is a recursive function that traverses the PDF's `outline` (list of bookmarks).
    *   `outline_items`: The current list of bookmarks or sub-bookmarks being processed.
    *   `reader`: The `PdfReader` object to resolve bookmark destinations to page numbers.
    *   `target_level`: The user-specified depth for chapter bookmarks (e.g., 0 for top-level, 1 for their children).
    *   `current_level`: Keeps track of the current recursion depth.
    *   It collects bookmarks (`Destination` objects) that are at the `target_level` and stores their title and starting page number.
3.  **`get_available_bookmark_levels(outline_items, current_level=0)`:**
    *   This helper function scans the entire bookmark tree to find out at which nesting levels actual `Destination` objects (page links) exist. This is useful for guiding the user if they pick a `chapter_level` with no bookmarks.
4.  **`extract_chapters_from_pdf(...)`:**
    *   **Setup:** Takes input PDF path, output directory, and `chapter_level`. Creates the output directory.
    *   **Read PDF:** Opens the PDF using `PdfReader`.
    *   **Get Bookmarks:** Retrieves `reader.outline`. If no outline exists, it exits.
    *   **Find Chapter Starts:** Calls `find_chapter_bookmarks` to get a list of potential chapters (dictionaries with `title`, `start_page`, `level`).
    *   **Sort & Filter:**
        *   Sorts these chapters by their `start_page`. This is crucial for correctly determining the end page of each chapter.
        *   Removes duplicate entries that might point to the same page (e.g., if "Chapter 1" and "1. Introduction" both link to page 5, only one is kept).
    *   **Iterate and Extract:**
        *   For each identified chapter start:
            *   The `start_page_idx` is known.
            *   The `end_page_idx` is determined by looking at the `start_page` of the *next* chapter. It's `next_chapter_start_page - 1`.
            *   For the very last chapter, its `end_page_idx` is the last page of the PDF.
            *   It performs sanity checks to ensure `start_page_idx <= end_page_idx` and that indices are within the PDF's bounds.
            *   A new `PdfWriter` object is created for the current chapter.
            *   Pages from `start_page_idx` to `end_page_idx` (inclusive) are copied from the `reader` to the `writer`.
            *   The `writer`'s content is saved to a new PDF file in the `output_dir`, named using a sanitized version of the chapter title (with a numeric prefix for ordering).
5.  **`if __name__ == "__main__":`:**
    *   This block sets up `argparse` to allow running the script from the command line with arguments for the PDF file, output directory, and chapter level.

**How to Run:**

1.  Save the code as a Python file (e.g., `pdf_chapter_extractor.py`).
2.  Open your terminal or command prompt.
3.  Run the script:

    ```bash
    python pdf_chapter_extractor.py "path/to/your/document.pdf"
    ```
    This will use the default output directory (`chapters_output`) and chapter level (0).

    To specify options:
    ```bash
    python pdf_chapter_extractor.py "my_book.pdf" -o "my_book_chapters" -l 1
    ```
    This would:
    *   Process `my_book.pdf`.
    *   Save chapters into the `my_book_chapters` directory.
    *   Consider bookmarks at nesting level 1 as chapters (e.g., if top-level bookmarks are "Part I", "Part II", and level 1 bookmarks are "Chapter 1", "Chapter 2" under each part).

**Important Considerations:**

*   **PDF Structure:** The script heavily relies on the PDF having a well-structured bookmark outline. The quality of the output depends entirely on this.
*   **`chapter_level`:** You might need to experiment with the `--chapter-level` (or `-l`) argument.
    *   Level 0: Top-most bookmarks.
    *   Level 1: Bookmarks nested one level deep.
    *   And so on.
    If you choose a level that has no bookmarks, the script will tell you which levels *do* have bookmarks.
*   **Ambiguous Bookmarks:** If bookmarks are not clearly demarcating chapters (e.g., many minor section bookmarks at the same level as chapter bookmarks), the results might not be perfect. The script tries to handle cases where consecutive bookmarks might point to the same page by skipping "empty" chapters.
*   **Encrypted PDFs:** If the PDF is encrypted and disallows content extraction, `pypdf` might not be able to read it or its outline.
*   **Corrupted PDFs:** Errors might occur with badly formed or corrupted PDFs.
