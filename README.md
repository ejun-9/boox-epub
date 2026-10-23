# boox-epub

Scripts behind my `boox-epub` Claude skill, built specifically for my own Boox Palma (6.13" monochrome e-ink, 824 × 1648 px, 300 ppi). They turn `.txt`, `.md` and PDF files, and web articles, into clean, reflowable EPUBs that read well on it.

Every layout choice is tuned to that one screen: a narrow column, so text is left-aligned with hyphenation rather than justified; pure black, because greys dither on e-ink; and tables that only stay as tables when they fit about 40 characters. They will open on other e-readers, but they are not designed for them.

| File | What it does |
|---|---|
| `palma_epub.py` | `.txt` / `.md` / `.pdf` → clean text → EPUB. Strips running headers and page numbers, rejoins paragraphs, finds chapters, OCRs scans. `--paper` adds figures, tables and linked citations. |
| `url_to_md.py` | Web article (URL or saved `.html`) → clean Markdown with its pictures, ready for `palma_epub.py`. |

The stylesheet forces no font, size or colour, so the reader's own controls keep working.

## Use

```bash
pip install pymupdf trafilatura        # pymupdf for PDFs, trafilatura for web links
python3 palma_epub.py book.pdf --title "Title" --author "Author" -o book.epub
python3 url_to_md.py "https://example.com/article" -o article.md && python3 palma_epub.py article.md -o article.epub
```

Needs `pandoc` and `poppler-utils`; `tesseract-ocr` for scanned PDFs. Run either script with `-h` for every option.
