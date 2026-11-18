#!/usr/bin/env python3
"""
pdf2epub.py - Convert a text-based PDF book into a reflowable EPUB (for Kindle, Kobo, etc.)

What it does
  * Uses the PDF's font sizes to tell body text, headings, quotes and footnotes apart
  * Removes running headers, footers and page numbers
  * Rejoins paragraphs split across pages and words hyphenated across lines
  * Keeps italics and bold
  * Turns footnotes into tappable pop-up notes
  * Starts a new chapter (and table-of-contents entry) at each large heading
  * Uses the first page as the cover

Requirements (once):
    pip install pymupdf ebooklib

Usage:
    python pdf2epub.py book.pdf
    python pdf2epub.py book.pdf -o book.epub --title "Title" --author "Author" --lang es
    python pdf2epub.py book.pdf --skip 651-656        # leave out pages (e.g. a printed index)
    python pdf2epub.py book.pdf --no-cover            # don't use page 1 as a cover
    python pdf2epub.py book.pdf --cover-image art.jpg # use your own cover image (JPEG/PNG); page 1 stays in the book

Limits: scanned PDFs (images of pages) have no text to extract and need OCR first.
Complex layouts (multi-column, tables, heavy images) convert less cleanly.
"""
import argparse, collections, html, os, re, sys, uuid

try:
    import pymupdf
except ImportError:
    import fitz as pymupdf
from ebooklib import epub

# ---------------------------------------------------------------- arguments
ap = argparse.ArgumentParser(description='Convert a text-based PDF book to EPUB.')
ap.add_argument('pdf')
ap.add_argument('-o', '--output')
ap.add_argument('--title')
ap.add_argument('--author')
ap.add_argument('--lang', help='language code, e.g. es, en (guessed if omitted)')
ap.add_argument('--skip', default='', help='pages to leave out, e.g. "1-4,651-656"')
ap.add_argument('--no-cover', action='store_true', help='do not use page 1 as the cover')
ap.add_argument('--cover-image', help='use this JPEG/PNG image as the cover instead of page 1')
args = ap.parse_args()

doc = pymupdf.open(args.pdf)
out_path = args.output or os.path.splitext(args.pdf)[0] + '.epub'

skip = set()
for part in filter(None, args.skip.replace(' ', '').split(',')):
    a, _, b = part.partition('-')
    skip.update(range(int(a), int(b or a) + 1))
use_cover = not args.no_cover and not args.cover_image
if use_cover:
    skip.add(1)

def esc(t): return html.escape(t, quote=False)

# ---------------------------------------------------------------- analyse fonts
size_chars = collections.Counter()
for pg in doc:
    for b in pg.get_text('dict')['blocks']:
        for l in b.get('lines', []):
            for s in l['spans']:
                size_chars[round(s['size'] * 2) / 2] += len(s['text'].strip())
BODY = size_chars.most_common(1)[0][0]
if not size_chars or sum(size_chars.values()) < 500:
    sys.exit('This PDF has (almost) no selectable text - it is probably scanned and needs OCR first.')

H = doc[0].rect.height
TOP, BOTTOM = H * 0.09, H * 0.91

def is_sc(s): return bool(re.search(r'SC\d*|SmallCap|Caps', s['font']))
def is_bold(s): return 'Bold' in s['font'] or 'Black' in s['font'] or 'Heavy' in s['font'] or (s['flags'] & 16)
def is_ital(s): return 'Italic' in s['font'] or 'Oblique' in s['font'] or (s['flags'] & 2)
def is_super(s, line_size):
    return s['text'].strip().isdigit() and len(s['text'].strip()) <= 3 and \
        (s['flags'] & 1 or s['size'] < line_size * 0.75)

# repeated header/footer text (digits stripped) across pages
edge_count = collections.Counter()
for pg in doc:
    seen = set()
    for b in pg.get_text('dict')['blocks']:
        for l in b.get('lines', []):
            y = l['bbox'][1]
            if y < TOP or y > BOTTOM:
                t = re.sub(r'\d+', '#', ''.join(s['text'] for s in l['spans'])).strip().lower()
                if t: seen.add(t)
    edge_count.update(seen)
REPEATED = {t for t, c in edge_count.items() if c >= max(3, len(doc) * 0.15)}

def is_edge_junk(line, txt):
    y = line['bbox'][1]
    if not (y < TOP or y > BOTTOM): return False
    norm = re.sub(r'\d+', '#', txt).strip().lower()
    return txt.strip().isdigit() or norm in REPEATED or re.fullmatch(r'[#\s\-–—|.]*', norm) is not None

INLINE_REF = r'(?<=[A-Za-zÀ-ÿ.,;:»”"\)])(\d{1,3})(?=[\s,.;:]|$)'

def span_html(s, upper=False):
    t = s['text'].upper() if upper else s['text']
    t = esc(t)
    if t.strip():
        if is_ital(s): t = f'<i>{t}</i>'
        if is_bold(s) and not is_sc(s): t = f'<b>{t}</b>'
    return t

def join_lines(lines):
    """Join (html, plain) lines into one paragraph, undoing end-of-line hyphenation."""
    oh, op = '', ''
    for h, p in lines:
        if op.rstrip().endswith('-') and p.lstrip()[:1].islower():
            oh = re.sub(r'-((?:</[ib]>)*)\s*$', r'\1', oh.rstrip())
            op = op.rstrip()[:-1]
            oh += h.lstrip(); op += p.lstrip()
        else:
            if op and not op.endswith(' '):
                oh += ' '; op += ' '
            oh += h; op += p
    oh = re.sub(r'</i>(\s*)<i>', r'\1', oh)
    oh = re.sub(r'</b>(\s*)<b>', r'\1', oh)
    return re.sub(r'\s+', ' ', oh).strip(), re.sub(r'\s+', ' ', op).strip()

# ---------------------------------------------------------------- pass 1: read pages
items = []   # (kind, html, plain, page)
notes = {}   # page -> [[num, [(html, plain), ...]], ...]

for pno in range(len(doc)):
    if pno + 1 in skip: continue
    page = doc[pno]
    blocks = [b for b in page.get_text('dict')['blocks'] if b.get('lines')]
    blocks.sort(key=lambda b: (round(b['bbox'][1]), b['bbox'][0]))

    body_x = [l['bbox'][0] for b in blocks for l in b['lines']
              if any(abs(s['size'] - BODY) < .6 for s in l['spans'])]
    margin = min(body_x) if body_x else 0

    # note numbers referenced on this page (superscripts or digits glued to words)
    refs = set()
    for b in blocks:
        for l in b['lines']:
            lsize = max(s['size'] for s in l['spans'])
            for s in l['spans']:
                if lsize >= BODY * 0.9 and is_super(s, lsize): refs.add(s['text'].strip())
                elif abs(s['size'] - BODY) < 1: refs.update(re.findall(INLINE_REF, s['text']))

    page_notes, page_start = [], len(items)
    for b in blocks:
        lines, foot = [], False
        for l in b['lines']:
            spans = l['spans']
            txt = ''.join(s['text'] for s in spans).strip()
            if not txt or is_edge_junk(l, txt): continue
            real = [s for s in spans if s['text'].strip()]
            lsize = max(s['size'] for s in real)
            if l['bbox'][1] > H * 0.45 and lsize < BODY * 0.8:
                foot = True
            h = ''.join(f'\x00REF{s["text"].strip()}\x00' if (not foot and is_super(s, lsize) and lsize >= BODY * 0.9)
                        else span_html(s, upper=is_sc(s)) for s in spans)
            p = ''.join(s['text'] for s in spans if foot or not (is_super(s, lsize) and lsize >= BODY * 0.9))
            lines.append((h, p, l))
        if not lines: continue

        if foot:
            for h, p, l in lines:
                m = re.match(r'^\s*(\d{1,3})\s*(.*)$', p)
                ok = m and re.match(r'^\s*(?:<[ib]>)?\s*\d', h) and not re.match(r'^\s*\d+\s*[)\-.,]', p) and (
                    m.group(1) in refs or (page_notes and page_notes[-1][0].isdigit()
                                           and int(m.group(1)) == int(page_notes[-1][0]) + 1))
                if ok:
                    hh = re.sub(r'^(\s*(?:<[ib]>)?\s*)\d+\s*', r'\1', h, count=1)
                    page_notes.append([m.group(1), [(hh, m.group(2))]])
                elif page_notes:
                    page_notes[-1][1].append((h, p))
                else:
                    items.append(('notecont', h, p, pno))
            continue

        # split a block where an all-bold line (inline section title) meets normal text
        def line_bold(l):
            r = [x for x in l['spans'] if x['text'].strip()]
            return bool(r) and all(is_bold(x) and not is_sc(x) and x['size'] >= BODY * 0.95 for x in r)
        groups = []
        for ln in lines:
            bb = line_bold(ln[2])
            if groups and groups[-1][0] == bb: groups[-1][1].append(ln)
            else: groups.append([bb, [ln]])
        groups = [g[1] for g in groups] if len(groups) > 1 else [lines]

        for glines in groups:
            sp = [x for _, _, l in glines for x in l['spans'] if x['text'].strip()]
            h, p = join_lines([(a, c) for a, c, _ in glines])
            big = max(x['size'] for x in sp)
            x0 = min(l['bbox'][0] for _, _, l in glines)
            if (big >= BODY * 1.3 or (any(is_sc(x) for x in sp) and big >= BODY * 0.8)) and len(p) < 200:
                kind = 'h1'
                h = '<br/>'.join(join_lines([(a, '')])[0] for a, _, _ in glines)
                p = ' | '.join(re.sub(r'\s+', ' ', c).strip() for _, c, _ in glines)
            elif all(is_bold(x) for x in sp) and len(p) < 140 and big >= BODY * 0.95:
                kind = 'h2'
            elif big >= BODY * 1.1 and len(p) < 140:
                kind = 'h2'
            elif big <= BODY * 0.7:
                kind = 'small'
            elif big <= BODY * 0.9:
                kind = 'quote'
            elif margin and x0 > margin + 25:
                kind = 'center' if len(p) < 60 else 'quote'
            else:
                kind = 'p'
            items.append((kind, h, p, pno))

    notes[pno] = page_notes
    nums = {n[0] for n in page_notes}
    if nums:
        for k in range(page_start, len(items)):
            kind, h, p, pg = items[k]
            if kind in ('p', 'quote', 'h2'):
                h = re.sub(INLINE_REF, lambda m: f'\x00REF{m.group(1)}\x00' if m.group(1) in nums else m.group(1), h)
                items[k] = (kind, h, p, pg)

# footnotes that continue from the previous page
for kind, h, p, pno in items:
    if kind == 'notecont':
        for pp in range(pno - 1, -1, -1):
            if notes.get(pp):
                notes[pp][-1][1].append((h, p)); break
items = [it for it in items if it[0] != 'notecont']

# ---------------------------------------------------------------- pass 2: merge
merged = []
for kind, h, p, pno in items:
    if merged:
        pk, ph, pp, ppno = merged[-1]
        if kind == 'h1' and pk == 'h1' and pno == ppno:
            merged[-1] = ('h1', ph + '<br/>' + h, pp + ' | ' + p, ppno); continue
        if kind == pk and kind in ('p', 'quote', 'small') and pno > ppno \
                and not re.search(r'[.!?:;»”"\)…]\s*$', pp):
            hj, pj = join_lines([(ph, pp), (h, p)])
            merged[-1] = (kind, hj, pj, pno); continue
    merged.append((kind, h, p, pno))

# ---------------------------------------------------------------- pass 3: chapters + notes
fn = [0]; used = set()
def resolve(h, pno, chapter_notes):
    def rep(m):
        num = m.group(1)
        for pp in (pno, pno - 1, pno + 1):
            for idx, n in enumerate(notes.get(pp, [])):
                if n[0] == num and (pp, idx) not in used:
                    used.add((pp, idx)); fn[0] += 1
                    fid = f'fn{fn[0]}'
                    chapter_notes.append((fid, num, join_lines(n[1])[0], True))
                    return f'<a epub:type="noteref" id="r{fid}" href="#{fid}"><sup>{num}</sup></a>'
        return f'<sup>{num}</sup>'
    return re.sub(r'\x00REF(\d+)\x00', rep, h)

def nice_title(plain):
    parts = [re.sub(r'\s+', ' ', x).strip(' *') for x in plain.split('|') if x.strip()]
    parts = [x.capitalize() if x.isupper() else x for x in parts]
    while len(parts) > 1 and parts[1][:1].islower():               # a title wrapped onto two lines
        parts[0:2] = [parts[0] + ' ' + parts[1]]
    if not parts: return 'Sin título'
    t = parts[0]
    if len(parts) > 1 and (len(t) < 25 or t.endswith(':')):   # e.g. "Capítulo I" + its name
        t = f'{t}. {parts[1]}' if not t.endswith(':') else f'{t} {parts[1]}'
    t = re.sub(r'(?i)\b(cap[ií]tulo|chapter|parte?|libro|book|chapitre|kapitel)\s+([ivxlc]+)\b',
               lambda m: f'{m.group(1)} {m.group(2).upper()}', t)
    t = t[0].upper() + t[1:]
    return t if len(t) <= 90 else t[:87].rsplit(' ', 1)[0] + '…'

chapters = []   # dict(title, parts, notes, first, last)
def new_chapter(title):
    chapters.append(dict(title=title, parts=[], notes=[], first=None, last=None))
new_chapter('Inicio')
for kind, h, p, pno in merged:
    if kind == 'h1':
        if chapters[-1]['parts']: new_chapter(nice_title(p))
        else: chapters[-1]['title'] = nice_title(p)
        chapters[-1]['parts'].append(f'<h1>{h}</h1>')
    else:
        ch = chapters[-1]
        h = resolve(h, pno, ch['notes'])
        ch['parts'].append({'p': '<p>{}</p>', 'h2': '<h2>{}</h2>', 'quote': '<blockquote><p>{}</p></blockquote>',
                            'small': '<p class="small">{}</p>', 'center': '<p class="center">{}</p>'}[kind].format(h))
    c = chapters[-1]
    c['first'] = pno if c['first'] is None else c['first']; c['last'] = pno

# notes whose reference number couldn't be found: keep them at the end of their chapter
orphans = 0
for pp in sorted(notes):
    for idx, n in enumerate(notes[pp]):
        if (pp, idx) in used: continue
        for c in chapters:
            if c['first'] is not None and c['first'] <= pp <= c['last']:
                fn[0] += 1; orphans += 1
                c['notes'].append((f'fn{fn[0]}', n[0], join_lines(n[1])[0], False)); break

# ---------------------------------------------------------------- metadata
meta = doc.metadata or {}
title = args.title or (meta.get('title') or '').strip() or os.path.splitext(os.path.basename(args.pdf))[0]
author = args.author or (meta.get('author') or '').strip() or 'Desconocido'
if args.lang:
    lang = args.lang
else:
    sample = ' '.join(it[2] for it in merged[:400]).lower().split()
    stop = {'es': {'de', 'la', 'que', 'el', 'y', 'los', 'en', 'las', 'por', 'una'},
            'en': {'the', 'and', 'of', 'to', 'in', 'that', 'is', 'was', 'for', 'with'},
            'fr': {'le', 'les', 'des', 'et', 'est', 'une', 'dans', 'pour', 'qui', 'du'},
            'pt': {'de', 'que', 'não', 'uma', 'os', 'do', 'da', 'em', 'para', 'com'},
            'de': {'der', 'die', 'und', 'das', 'ist', 'nicht', 'mit', 'den', 'ein', 'zu'},
            'it': {'il', 'di', 'che', 'e', 'la', 'per', 'non', 'una', 'del', 'gli'}}
    lang = max(stop, key=lambda k: sum(w in stop[k] for w in sample))

# ---------------------------------------------------------------- build EPUB
book = epub.EpubBook()
book.set_identifier('urn:uuid:' + str(uuid.uuid4()))
book.set_title(title); book.set_language(lang); book.add_author(author)
if args.cover_image:
    try:
        pix = pymupdf.Pixmap(args.cover_image)
    except Exception:
        sys.exit('The cover image could not be read - please use a JPEG or PNG file.')
    if pix.alpha: pix = pymupdf.Pixmap(pix, 0)                  # JPEG has no transparency
    if pix.n > 3: pix = pymupdf.Pixmap(pymupdf.csRGB, pix)       # e.g. CMYK
    book.set_cover('cover.jpg', pix.tobytes('jpeg', jpg_quality=90))
elif use_cover:
    book.set_cover('cover.jpg', doc[0].get_pixmap(dpi=200).tobytes('jpeg', jpg_quality=85))

css = epub.EpubItem(uid='css', file_name='style.css', media_type='text/css', content=b'''
p { text-indent: 1.2em; margin: 0; text-align: justify; }
h1 + p, h2 + p, blockquote + p { text-indent: 0; }
h1 { text-align: center; font-size: 1.4em; margin: 2em 0 1.5em; line-height: 1.3; }
h2 { font-size: 1.1em; margin: 1.5em 0 0.6em; }
blockquote { margin: 0.8em 1.5em; font-size: 0.92em; }
blockquote p { text-indent: 0; }
p.small { font-size: 0.85em; text-indent: 0; margin: 0.5em 0; }
p.center { text-align: center; text-indent: 0; margin: 0.4em 0; }
aside.fn p { text-indent: 0; font-size: 0.85em; margin: 0.4em 0; }
hr.notes { margin: 2em 30%; }
''')
book.add_item(css)

spine, toc = ['nav'], []
for i, c in enumerate(chapters):
    if not c['parts']: continue
    body = '\n'.join(c['parts'])
    if c['notes']:
        body += '\n<hr class="notes"/>\n'
        for fid, num, text, linked in c['notes']:
            if linked:
                body += f'<aside epub:type="footnote" class="fn" id="{fid}"><p><a href="#r{fid}">{num}</a>. {text}</p></aside>\n'
            else:
                body += f'<aside class="fn" id="{fid}"><p>{num}. {text}</p></aside>\n'
    item = epub.EpubHtml(title=c['title'], file_name=f'c{i:03d}.xhtml', lang=lang)
    item.content = f'<html><head><title>{esc(c["title"])}</title></head><body>{body}</body></html>'
    item.add_item(css)
    book.add_item(item); spine.append(item)
    if c['parts'][0].startswith('<h1>'): toc.append(item)
book.toc = toc; book.spine = spine
book.add_item(epub.EpubNcx()); book.add_item(epub.EpubNav())
epub.write_epub(out_path, book)

print(f'Saved {out_path}')
print(f'  {len(toc)} chapters, {fn[0]} footnotes ({orphans} could not be linked to their number)')
for c in chapters:
    if c['parts'] and c['parts'][0].startswith('<h1>'): print('   -', c['title'])
