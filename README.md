# PDF → EPUB

Turn text-based PDF books into clean, reflowable EPUBs for Kindle, Kobo and Apple Books, either from the command line or from a small drag-and-drop web page you host yourself.

## What the converter does

- Uses the PDF's font sizes to tell body text, headings, quotes and footnotes apart
- Removes running headers, footers and page numbers
- Rejoins paragraphs split across pages and words hyphenated across lines
- Keeps italics and bold
- Turns footnotes into tappable pop-up notes
- Starts a new chapter (and table-of-contents entry) at each large heading
- Uses the first page as the cover, or an image you choose

Scanned PDFs (images of pages) have no text to extract and need OCR first. Multi-column layouts, tables and image-heavy books convert less cleanly.

## Web app

Drop one or more PDFs on the page and each EPUB downloads automatically when it's ready. Optional settings: title, author, language, pages to leave out, and the cover (first page, an uploaded JPEG/PNG, or none). It supports dark mode and works on phones.

Run it with Docker:

```sh
docker compose up -d --build
```

Then open `http://<your-machine's-IP>:8100` from any device on your network. Because `pdf2epub.py` is mounted into the container, changes to the script apply on the next conversion without a rebuild.

Limits: 300 MB per PDF, about 15 minutes per conversion.

## Command line

```sh
pip install pymupdf ebooklib

python pdf2epub.py book.pdf
python pdf2epub.py book.pdf -o book.epub --title "Title" --author "Author" --lang es
python pdf2epub.py book.pdf --skip 651-656         # leave out pages (e.g. a printed index)
python pdf2epub.py book.pdf --no-cover             # don't use page 1 as a cover
python pdf2epub.py book.pdf --cover-image art.jpg  # use your own cover; page 1 stays in the book
```

## Project layout

| File | Purpose |
| --- | --- |
| `pdf2epub.py` | The converter |
| `app.py` | Flask server that runs the converter on uploads |
| `static/index.html` | The drag-and-drop page |
| `Dockerfile`, `docker-compose.yml` | Container setup |

## License

MIT
