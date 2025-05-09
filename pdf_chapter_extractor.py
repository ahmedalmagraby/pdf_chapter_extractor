import os
import re
import argparse
from pypdf import PdfReader, PdfWriter

def sanitize_filename(name):
    """
    Sanitizes a string to be used as a valid filename.
    Removes or replaces characters that are not allowed in filenames.
    """
    if not name:
        return "untitled"
    # Remove characters that are invalid in Windows filenames
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    # Remove leading/trailing whitespace and replace multiple spaces with single
    name = re.sub(r'\s+', ' ', name).strip()
    # Limit length to avoid issues with max filename length
    name = name[:150]
    if not name: # If all characters were invalid
        return "untitled_chapter"
    return name

def find_chapter_bookmarks(outline_items, reader, target_level, current_level=0):
    """
    Recursively finds bookmarks at a specific target_level.
    An outline item can be a Destination (a bookmark) or a list (sub-bookmarks).
    """
    chapters = []
    for item in outline_items:
        if isinstance(item, list):
            # This is a list of sub-bookmarks, recurse if not deep enough
            if current_level < target_level:
                chapters.extend(find_chapter_bookmarks(item, reader, target_level, current_level + 1))
            # If current_level == target_level, it means we are at a list *at* the target level.
            # We might want its direct children if they are Destinations.
            # This case could be refined, but for now, we primarily look for Destinations at target_level.
        elif current_level == target_level:
            # This is a Destination object at the target level
            try:
                # .page is 0-indexed page number in PyPDF2.
                # For pypdf, get_destination_page_number is more robust.
                page_num = reader.get_destination_page_number(item)
                if page_num is not None: # Ensure it's a valid internal link
                    chapters.append({
                        "title": str(item.title), # Ensure it's a string
                        "start_page": page_num,
                        "level": current_level
                    })
            except Exception as e:
                print(f"  Warning: Could not get page for bookmark '{item.title}': {e}")
    return chapters

def get_available_bookmark_levels(outline_items, current_level=0):
    """
    Recursively finds all levels at which bookmarks (Destinations) exist.
    """
    levels = set()
    for item in outline_items:
        if isinstance(item, list):
            levels.update(get_available_bookmark_levels(item, current_level + 1))
        else: # It's a Destination
            levels.add(current_level)
    return levels


def extract_chapters_from_pdf(pdf_path, output_dir="chapters_output", chapter_level=0):
    """
    Extracts chapters from a PDF based on its bookmarks at a specified level.

    Args:
        pdf_path (str): Path to the input PDF file.
        output_dir (str): Directory to save the extracted chapter PDFs.
        chapter_level (int): The nesting level of bookmarks to consider as chapters
                             (0 for top-level, 1 for next level, etc.).
    """
    if not os.path.exists(pdf_path):
        print(f"Error: PDF file not found at '{pdf_path}'")
        return

    os.makedirs(output_dir, exist_ok=True)
    print(f"Processing '{pdf_path}'...")

    try:
        reader = PdfReader(pdf_path)
    except Exception as e:
        print(f"Error opening or parsing PDF: {e}")
        return

    if not reader.outline:
        print("No bookmarks (outline) found in this PDF. Cannot extract chapters automatically.")
        return

    # Find bookmarks at the specified chapter_level
    chapter_starts = find_chapter_bookmarks(reader.outline, reader, chapter_level)

    if not chapter_starts:
        available_levels = get_available_bookmark_levels(reader.outline)
        print(f"No bookmarks found at level {chapter_level}.")
        if available_levels:
            print(f"Bookmarks found at levels: {sorted(list(available_levels))}. Please try one of these for --chapter-level.")
        else:
            print("No page-linking bookmarks found in the PDF at all.")
        return

    # Sort chapters by their start page (essential for determining end pages)
    chapter_starts.sort(key=lambda x: x['start_page'])

    # Remove duplicate chapter starts (e.g. "Chapter 1" and "1. Intro" point to same page)
    # Keep the first one encountered for a given page after sorting.
    unique_chapter_starts = []
    seen_pages = set()
    for ch_info in chapter_starts:
        if ch_info['start_page'] not in seen_pages:
            unique_chapter_starts.append(ch_info)
            seen_pages.add(ch_info['start_page'])
    chapter_starts = unique_chapter_starts
    
    if not chapter_starts: # Should not happen if previous check passed, but good practice
        print("No valid chapter starting points found after filtering.")
        return

    num_pages_total = len(reader.pages)
    print(f"Found {len(chapter_starts)} potential chapters at level {chapter_level}.")

    for i in range(len(chapter_starts)):
        current_chapter_info = chapter_starts[i]
        title = current_chapter_info['title']
        start_page_idx = current_chapter_info['start_page'] # 0-indexed

        if i + 1 < len(chapter_starts):
            # End page is one less than the start of the next chapter
            next_chapter_start_page_idx = chapter_starts[i+1]['start_page']
            end_page_idx = next_chapter_start_page_idx - 1
        else:
            # This is the last chapter, so it goes to the end of the document
            end_page_idx = num_pages_total - 1

        # Sanity check: ensure chapter has at least one page
        if start_page_idx > end_page_idx:
            print(f"  Skipping chapter '{title}' (intended pages {start_page_idx+1}-{end_page_idx+1}): "
                  f"Calculated end page is before start page. This often happens if consecutive "
                  f"bookmarks at the chosen level point to the same page or if a sub-bookmark starts on the same page.")
            continue
        
        # Sanity check: ensure page indices are valid
        if not (0 <= start_page_idx < num_pages_total and 0 <= end_page_idx < num_pages_total):
            print(f"  Skipping chapter '{title}': Invalid page range ({start_page_idx+1}-{end_page_idx+1}) for a PDF with {num_pages_total} pages.")
            continue


        print(f"  Extracting: '{title}' (Pages {start_page_idx + 1} to {end_page_idx + 1})")

        writer = PdfWriter()
        for page_num in range(start_page_idx, end_page_idx + 1):
            try:
                writer.add_page(reader.pages[page_num])
            except Exception as e: # Page might be corrupted or inaccessible
                print(f"    Error adding page {page_num + 1} for chapter '{title}': {e}. Skipping page.")
                continue
        
        if not writer.pages:
            print(f"    Skipping chapter '{title}' as no pages could be added (possibly due to errors or empty range).")
            continue

        safe_file_title = sanitize_filename(title)
        # Add a prefix to ensure files are sorted somewhat chronologically if names are similar
        output_filename = f"{i+1:03d}_{safe_file_title}.pdf"
        output_pdf_path = os.path.join(output_dir, output_filename)

        try:
            with open(output_pdf_path, "wb") as f_out:
                writer.write(f_out)
            print(f"    Successfully saved: {output_pdf_path}")
        except Exception as e:
            print(f"    Error saving {output_pdf_path}: {e}")
    
    print("\nExtraction complete.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extract chapters from a PDF based on its bookmarks.")
    parser.add_argument("pdf_path", help="Path to the input PDF file.")
    parser.add_argument("-o", "--output_dir", default="chapters_output",
                        help="Directory to save the extracted chapter PDFs (default: chapters_output).")
    parser.add_argument("-l", "--chapter_level", type=int, default=0,
                        help="Nesting level of bookmarks to consider as chapters (0 for top-level, 1 for next, etc. Default: 0).")

    args = parser.parse_args()

    extract_chapters_from_pdf(args.pdf_path, args.output_dir, args.chapter_level)