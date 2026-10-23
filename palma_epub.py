#!/usr/bin/env python3
"""palma_epub.py: .txt / .md / .pdf -> clean text -> reflowable EPUB for the Boox Palma."""
import argparse, collections, contextlib, html, io, pathlib, re, shutil, subprocess, sys, tempfile, zipfile
import xml.dom.minidom

META, WARN, INFO = {}, [], []
SOFT = chr(173)                       # soft hyphen: marks a line-break-only hyphen (built with chr() so it cannot be lost)
VOCAB = collections.Counter()         # every word seen unbroken in the source; VOCAB[SOFT] counts soft hyphens found
NUM = (r'(\d+|[ivxlcdm]+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|'
       r'fifteen|sixteen|seventeen|eighteen|nineteen|twenty\S*|thirty\S*|forty\S*|fifty\S*|first|second|third|last)')
HEAD = (r'(?i)^((chapter|part|book|volume|section|cap[ií]tulo|parte|chapitre|kapitel)\s+' + NUM + r'\b.{0,70}(?<![,;])'
        r'|prologue|epilogue|introduction|preface|foreword|afterword|conclusion|appendix(\s+\w+)?|abstract'
        r'|acknowledge?ments|(table of )?contents|bibliography|references|notes)[.:]?$')
LABEL = re.compile(r'(?i)^(chapter|part|book|volume|section|cap[ií]tulo|parte|chapitre|kapitel)\s+\S+[.:]?$')
END = r'[.!?:;"”’»)\]…]$'      # a line ending like this probably ends a sentence
LOW = r'[a-zà-ÿ]'
CAP = re.compile(r'(?i)^(figure|fig\.|table|listing|algorithm)\s*\d+')
CAPSTART = re.compile(r'(Figure|Fig\.|Table)\s*(\d+)\s*(?:[:.]\s*\S|$)')    # a caption (or a bare 'Table 1' line above one), not a sentence about one
NUMH = re.compile(r'\d{1,2}(\.\d{1,2}){0,2}\.?\s+[^\W\d_a-z]')                # "3.1 Method"
REFH = re.compile(r'(?i)^(\d+\.?\s+)?(references|bibliography|works cited)$')
ITEM = re.compile(r'(•|\(\d{1,2}\)|\[\d{1,3}\])\s')                     # bullet, (1), [12]
BROKEN = re.compile(r'(?i)\b(signifc|specifc|scientifc|classifc|notifc|identifed|modifed|defn|fnd|frst\b|fnal|fgur|'
                    r'efcien|difcult|ofce|sufc|benefc|confg|infuen|fexib|workfow)\w*')
CSS = """/* Boox Palma: 824x1648 px, 300 ppi, 6.13in e-ink. No fonts, sizes or colours are forced,
   so NeoReader's own font, size, spacing and margin controls keep working. */
html, body { margin: 0; padding: 0; }
body { line-height: 1.45; text-align: left; hyphens: auto; -webkit-hyphens: auto; -epub-hyphens: auto;
       widows: 2; orphans: 2; color: #000; }
p { margin: 0; text-indent: 1.3em; }
h1 + p, h2 + p, h3 + p, hr + p, li p, td p, blockquote > p:first-child, p.author, p.date { text-indent: 0; }
h1, h2, h3 { line-height: 1.2; text-align: left; hyphens: none; -webkit-hyphens: none;
             page-break-after: avoid; break-after: avoid; }
h1 { font-size: 1.45em; margin: 1.5em 0 1em; }
h2 { font-size: 1.15em; margin: 1.6em 0 0.6em; }
h3 { font-size: 1em; margin: 1.4em 0 0.4em; }
hr { border: 0; border-top: 1px solid #000; width: 30%; margin: 1.5em auto; }
blockquote { margin: 1em 0 1em 1.2em; }
div.verse { margin: 1em 0 1em 1.2em; text-indent: 0; hyphens: none; }
ul, ol { margin: 0.8em 0; padding-left: 1.4em; }
img { max-width: 100%; height: auto; }
figure { margin: 1em 0; text-align: center; page-break-inside: avoid; }
figcaption { font-size: 0.85em; }
pre, code { font-size: 0.8em; white-space: pre-wrap; overflow-wrap: break-word; }
table { font-size: 0.8em; border-collapse: collapse; width: 100%; margin: 1em 0; }
th, td { border: 1px solid #000; padding: 0.2em 0.3em; vertical-align: top; text-align: left; }
div.li p { margin-left: 1.3em; text-indent: -1.3em; }
div.ref p { margin: 0 0 0.5em 1.8em; text-indent: -1.8em; font-size: 0.9em; hyphens: none; -webkit-hyphens: none; }
div.cap p { text-indent: 0; margin: 1em 0 0.4em; font-size: 0.85em; }
div.rec { margin: 0.7em 0; font-size: 0.9em; }
div.rec p { margin-left: 1em; text-indent: -1em; }
figcaption { text-align: left; }
a { color: inherit; text-decoration: underline; }
a.cite { text-decoration: none; }
sup, a.footnote-ref { line-height: 0; font-size: 0.7em; vertical-align: super; text-decoration: none; }
"""


def clean(s):
    for a, b in [(SOFT, '')] + list(zip(map(chr, range(0xfb00, 0xfb05)), ('ff', 'fi', 'fl', 'ffi', 'ffl'))):   # ligatures
        s = s.replace(a, b)
    return re.sub('[' + ''.join(map(chr, [*range(9), 11, 12, *range(14, 32), 0xfffe, 0xffff])) + ']', '', s)   # control characters


def learn(lines):
    """Remember each word that appears unbroken, so a line-end hyphen can be judged against the text itself."""
    for t in lines:
        ws = t.split()
        for w in (ws[:-1] if ws and ws[-1].endswith('-') else ws):
            VOCAB.update(re.findall(r'[^\W\d_]+(?:-[^\W\d_]+)*', w.lower()))


def join(lines, cjk=False):
    """Join wrapped lines into one paragraph, re-attaching words split by a line-end hyphen."""
    out = ''
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        if out.endswith(SOFT):
            out = out[:-1] + ln                         # the PDF marks this hyphen as a line break only
        elif (re.search(r'https?://\S*[/._?=&-]$', out) and re.match(r'\d+\b|\S*[/._=?&%-]\S', ln)
              and not (out[-1] == '.' and ln[0].isupper())):
            out += ln                                   # a URL wrapped over two lines
        elif re.search(LOW + '-$', out) and re.match(LOW, ln):
            a = re.search(r'([^\W\d_]+(?:-[^\W\d_]+)*)-$', out).group(1).lower()
            b = re.match(r'[^\W\d_]+', ln).group(0).lower()
            if VOCAB[a + b] or not (VOCAB[a + '-' + b] or '-' in a or VOCAB[SOFT]):
                out = out[:-1] + ln                     # "inter-" + "national"
            else:
                out += ln                               # "speech-" + "based": seen hyphenated, or a real hyphen
        elif re.search(r'\w-$', out) and re.match(r'\w', ln):
            out += ln                                   # "Trace-" + "Monkey", "IEEE-" + "754": keep the hyphen
        else:
            out += ('' if cjk or not out else ' ') + ln
    return out


def run(*cmd):
    return subprocess.run([str(c) for c in cmd], capture_output=True)


# ---------- plain text -> blocks ----------
def read_txt(path):
    raw = pathlib.Path(path).read_bytes()
    encs = ('utf-16',) if raw[:2] in (b'\xff\xfe', b'\xfe\xff') else ('utf-8-sig', 'cp1252')
    for enc in encs:
        try:
            return raw.decode(enc)
        except UnicodeError:
            pass
    return raw.decode('latin-1')


def txt_blocks(text, a):
    text = clean(text.replace('\r\n', '\n').replace('\r', '\n')).expandtabs(4)
    for k in ('Title', 'Author'):                      # Project Gutenberg style header
        m = re.search(rf'(?m)^{k}:\s*(\S.*)$', text[:4000])
        if m:
            META.setdefault(k.lower(), m.group(1).strip())
    s, e = re.search(r'\*\*\* ?START OF .*?\*\*\*', text, re.S), re.search(r'\*\*\* ?END OF ', text)
    if s and e:
        text = text[s.end():e.start()]
    lines = [l.rstrip() for l in text.split('\n')]
    learn(lines)
    lens = sorted(len(l) for l in lines if l.strip())
    if not lens:
        return []
    p90 = lens[int(len(lens) * .9)]
    wrapped = p90 <= 100                               # hard-wrapped at a fixed column
    dense = lines.count('') < .03 * len(lines)         # paragraphs not separated by blank lines
    raw, cur = [], []
    for l in lines:
        if not l.strip():
            if cur:
                raw.append(cur)
                cur = []
        elif cur and (not wrapped or (dense and (re.match(r'\s{2,}\S', l) or
                      (len(cur[-1]) < .7 * p90 and re.search(END, cur[-1]))))):
            raw.append(cur)
            cur = [l]
        else:
            cur.append(l)
    if cur:
        raw.append(cur)
    hre, out = re.compile(a.heading_regex or HEAD), []
    for ls in raw:
        one = ' '.join(x.strip() for x in ls)
        m = re.match(r'(#{1,3})\s+(.+)', one)
        if len(ls) == 1 and m:
            out.append(['h', len(m.group(1)), m.group(2).strip('# ')])
        elif re.fullmatch(r'([*\-_~=#•·⁂]\s*){3,}', one):
            out.append(['hr'])
        elif len(ls) <= 2 and len(one) <= 90 and hre.search(one):
            out.append(['h', 1, one])
        elif len(ls) == 1 and re.fullmatch(r'([IVXLC]+|\d{1,3})\.?', one):
            out.append(['num', one])
        elif (len(ls) == 1 and 3 <= len(one) <= 60 and one.upper() == one and re.search(r'[^\W\d_]{2}', one)
              and one.lower() != one and re.fullmatch(r'\w.*[\w)]', one) and 'THE END' not in one):
            out.append(['caps', one])
        elif wrapped and len(ls) > 1 and max(map(len, ls)) < .6 * p90:
            out.append(['v', [x.strip() for x in ls]])  # short lines kept as-is: verse, lists, addresses
        else:
            out.append(['p', join(ls, a.cjk)])
    nums, caps = sum(b[0] == 'num' for b in out), sum(b[0] == 'caps' for b in out)
    has_h1 = nums >= 3 or any(b[0] == 'h' and b[1] == 1 for b in out)
    caps_ok = not a.no_caps_headings and not has_h1 and 2 <= caps <= max(2, len(out) / 6)   # only if no "Chapter N" lines
    for n, b in enumerate(out):
        if b[0] == 'num':
            b[:] = ['h', 1, b[1]] if nums >= 3 else ['p', b[1]]
        elif b[0] == 'caps':
            named = n and out[n - 1][0] == 'h' and out[n - 1][1] == 1      # "CHAPTER I." then "THE FERRY"
            b[:] = ['h', 1, b[1]] if caps_ok or named else ['p', b[1]]
    return out


# ---------- PDF -> blocks ----------
def glyph_order(doc, xref):
    """Glyph names of an embedded font, in glyph-index order (None when they cannot be read)."""
    ref = lambda x, k: (lambda v: int(v[1].split()[0]) if v[0] == 'xref' else None)(doc.xref_get_key(x, k))
    fd = ref(xref, 'FontDescriptor')
    key, ff = next(((k, ref(fd, k)) for k in ('FontFile', 'FontFile3', 'FontFile2') if fd and ref(fd, k)), (None, None))
    if not ff:
        return None
    try:
        data = doc.xref_stream(ff)
        if key == 'FontFile':                           # Type 1: decrypt the eexec part and list /CharStrings
            n1, n2 = (int(doc.xref_get_key(ff, k)[1]) for k in ('Length1', 'Length2'))
            r, d = 55665, bytearray()
            for c in data[n1:n1 + n2]:
                d.append(c ^ (r >> 8))
                r = ((c + r) * 52845 + 22719) & 0xFFFF
            d = bytes(d[4:])
            p, names = d.index(b'begin', d.index(b'/CharStrings')) + 5, []
            while True:
                m = re.compile(rb'\s*(?:ND|\|-|noaccess def)?\s*/(\S+)\s+(\d+)\s+(?:RD|-\|)\s').match(d, p)
                if not m:
                    break
                names.append(m.group(1).decode('latin-1'))
                p = m.end() + int(m.group(2))
            if '.notdef' in names:                      # the renderer keeps .notdef in slot 0
                k = names.index('.notdef')
                names[0], names[k] = names[k], names[0]
            return names
        if key == 'FontFile3':
            from fontTools.cffLib import CFFFontSet
            cff = CFFFontSet()
            cff.decompile(io.BytesIO(data), None)
            return list(cff[0].charset)
        from fontTools.ttLib import TTFont
        return TTFont(io.BytesIO(data)).getGlyphOrder()
    except Exception:
        return None


def glyph_fixes(doc):
    """Per page: fi/fl/ff ligatures whose letters the PDF's font map lost, and true soft hyphens.
    Each entry is (x, y, the character the text layer shows, what it should be or None)."""
    orders = {}
    for n in range(len(doc)):
        for f in doc.get_page_fonts(n):
            base = f[3].split('+')[-1]
            if base not in orders:
                orders[base] = glyph_order(doc, f[0])
    spell = lambda g: (lambda p: ''.join(p) if len(p) > 1 and all(len(x) == 1 and x.isalpha() for x in p)
                       else p[0] if p[0] in ('ff', 'fi', 'fl', 'ffi', 'ffl') else '')(g.split('.')[0].split('_'))
    fixes, lost = {}, 0
    for n, page in enumerate(doc):
        ents = []
        for tr in page.get_texttrace():
            order, ch = orders.get(tr['font']), tr['chars']
            for k, (ucs, gid, org, _) in enumerate(ch):
                c, want = chr(ucs), None
                if order and 0 <= gid < len(order):
                    w = spell(order[gid])
                    if len(w) > 1 and w[0] == c and not (k + 1 < len(ch) and ch[k + 1][1] == -1):
                        want = w                        # one glyph, several letters, but only the first came through
                ents.append((org[0], org[1], c, want))
        bad = {e[2] for e in ents if e[3]}
        lost += sum(1 for e in ents if e[3])
        ents = [e for e in ents if e[2] in bad]
        if SOFT in page.get_text():                     # a tagged PDF says which line-end hyphens are only line breaks
            ents += [(c['origin'][0], c['origin'][1], SOFT, None) for b in page.get_text('rawdict')['blocks']
                     for l in b.get('lines', ()) for sp in l['spans'] for c in sp['chars'] if c['c'] == SOFT]
            VOCAB[SOFT] += sum(1 for e in ents if e[2] == SOFT)
        if ents:
            fixes[n] = ents
    if lost:
        INFO.append(f'FIXED  {lost} places where this PDF\'s font map had lost letters (fi, fl, ff): restored')
    return fixes


def patch(word, box, ents):
    """Apply glyph_fixes entries that fall inside one pdftotext word."""
    hit = [e for e in ents if box[0] - 1 <= e[0] <= box[2] + 1 and box[1] - 1 <= e[1] <= box[3] + 2]
    if any(e[2] == SOFT for e in hit) and word.endswith('-'):
        word = word[:-1] + SOFT
    for c in {e[2] for e in hit if e[3]}:
        seq, pos = [e for e in hit if e[2] == c], [k for k, x in enumerate(word) if x == c]
        if len(seq) == len(pos):                        # same letters in the same order: safe to line up
            for k, e in reversed(list(zip(pos, seq))):
                if e[3]:
                    word = word[:k] + e[3] + word[k + 1:]
    return word


def pdf_pages(path, fixes=None):
    """Text lines with their positions, grouped page > block, in reading order (columns handled by pdftotext)."""
    data = run('pdftotext', '-bbox-layout', '-enc', 'UTF-8', path, '-').stdout
    try:
        from lxml import etree as ET
        root = ET.fromstring(data, ET.XMLParser(recover=True, huge_tree=True)) if data.strip() else None
    except ImportError:
        import xml.etree.ElementTree as ET
        root = ET.fromstring(data) if data.strip() else None
    pages = []
    for n, pg in enumerate(root.iter('{*}page') if root is not None else ()):
        ents, blocks = (fixes or {}).get(n), []
        for bl in pg.iter('{*}block'):
            ls = []
            for ln in bl.iter('{*}line'):
                ws = []
                for w in ln.iter('{*}word'):
                    t = clean(w.text or '').strip()
                    if len(t) == 1 and not ws and (0x80 <= ord(t) <= 0x9f or 0xe000 <= ord(t) <= 0xf8ff):
                        t = '•'                         # a list bullet from a symbol font with no text mapping
                    if t:
                        box = [float(w.get(k)) for k in ('xMin', 'yMin', 'xMax', 'yMax')]
                        ws.append(box + [patch(t, box, ents) if ents else t])
                if ws:
                    x0, y0, x1, y1 = (float(ln.get(k)) for k in ('xMin', 'yMin', 'xMax', 'yMax'))
                    wh = sorted(w[3] - w[1] for w in ws)                                  # median word height:
                    ls.append(dict(x0=x0, y0=y0, x1=x1, y1=y1, h=wh[len(wh) // 2],        # immune to superscripts
                                   t=' '.join(w[4] for w in ws), w=ws))
            if ls:
                blocks.append(ls)
        flat = [l for b in blocks for l in b]
        for l in flat:                                  # "3.1" set apart from its title: put it back on that line
            if re.fullmatch(r'\d{1,2}(\.\d{1,2}){0,2}\.?', l['t']):
                o = next((o for o in flat if o is not l and not o.get('gone') and abs(o['y0'] - l['y0']) < 1
                          and abs(o['h'] - l['h']) < .3 and 0 < o['x0'] - l['x1'] < 2.5 * l['h']), None)
                if o:
                    o['t'], o['x0'], o['w'], l['gone'] = l['t'] + ' ' + o['t'], l['x0'], l['w'] + o['w'], True
        blocks = [[l for l in b if not l.get('gone')] for b in blocks]
        learn(l['t'] for b in blocks for l in b)
        pages.append((float(pg.get('height')), [b for b in blocks if b]))
    return pages


def ocr_pages(path, tmp, lang):
    """Scanned PDF: render every page and read it with tesseract, several pages at a time."""
    import concurrent.futures as cf
    import os
    WARN.append('No text layer: this PDF was OCRed. Expect some typos and rougher paragraph breaks.')
    d = tmp / 'ocr'
    d.mkdir()
    run('pdftoppm', '-r', 300, '-gray', '-png', path, d / 'p')
    env = dict(os.environ, OMP_THREAD_LIMIT='1')

    def one(png):
        r = subprocess.run(['tesseract', str(png), 'stdout', '-l', lang, 'tsv'], capture_output=True, env=env)
        ph, lines = 1.0, {}
        for x in (row.split('\t') for row in r.stdout.decode('utf-8', 'replace').splitlines()[1:]):
            if len(x) < 12:
                continue
            left, top, w, h = (float(v) for v in x[6:10])
            if x[0] == '1':
                ph = h
            elif x[0] == '4':
                lines[tuple(x[2:5])] = dict(x0=left, y0=top, x1=left + w, y1=top + h, h=h, t='')
            elif x[0] == '5' and tuple(x[2:5]) in lines:
                lines[tuple(x[2:5])]['t'] += x[11] + ' '
        blocks = {}
        for k, v in lines.items():
            v['t'] = clean(v['t']).strip()
            if v['t']:
                blocks.setdefault(k[0], []).append(v)
        return ph, list(blocks.values())

    with cf.ThreadPoolExecutor(os.cpu_count() or 2) as ex:
        return list(ex.map(one, sorted(d.glob('p*.png'))))


def paper_layout(doc, pages, a, imgdir, tag, bh):
    """Papers: tidy the title block, crop each figure, rebuild each table. Edits the pdftotext lines in place:
    'drop' takes a line out of the text flow, 'emit' puts ready-made blocks at that line's position."""
    box = lambda ls: (min(l['x0'] for l in ls), min(l['y0'] for l in ls), max(l['x1'] for l in ls), max(l['y1'] for l in ls))
    live = lambda blocks: [l for b in blocks for l in b if not l.get('drop')]
    front = False
    if pages:                                           # everything above "Abstract" on page 1: one byline per author
        ph, blocks = pages[0]
        ab = next((l for l in live(blocks) if re.fullmatch(r'(?i)abstract[.:]?', l['t']) and l['y0'] < .6 * ph), None)
        text = [min(l['y0'] for l in b) for b in blocks if len(b) >= 4 and abs(b[0]['h'] - bh) < .06 * bh]   # column text
        limit = min([ab['y0'] + 1] + text) if ab else 0
        above = [l for l in live(blocks) if l['y1'] <= limit and not CAPSTART.match(l['t'])]
        if above:
            norm, size = (lambda t: re.sub(r'\W+', '', t.lower())), (lambda l: round(l['h'] * 2) / 2)
            known, mid = norm(a.title or ''), (lambda l: (l['x0'] + l['x1']) / 2)
            title = ([l for l in above if len(norm(l['t'])) >= 4 and norm(l['t']) in known]
                     or [l for l in above if size(l) == max(map(size, above))])
            if not a.title:
                META['title'] = join(l['t'] for l in sorted(title, key=lambda l: l['y0']))
            span, groups = max(l['x1'] for l in above) - min(l['x0'] for l in above), []
            for l in sorted((l for l in above if not any(l is t for t in title)), key=lambda l: (round(l['y0']), l['x0'])):
                g = next((g for g in groups if abs(mid(g[-1]) - mid(l)) < .1 * span and -2 < l['y0'] - g[-1]['y1'] < .6 * l['h']), None)
                if g:
                    g.append(l)                         # same author: directly below, same centre line
                else:
                    groups.append([l])
            out = [['v', [l['t'] for l in g]] for g in groups]
            who = groups[0][0]['t'] if groups else ''
            if not a.author and who and len(who.split()) <= 5 and not re.search(r'[@\d]', who):
                META['author'] = who + (' et al' if len(groups) > 1 else '')
            for l in above:
                l['drop'] = True
            ab['emit'], front = [b for b in out if b[1]], True
    if doc is None:
        WARN.append('pymupdf is not installed, so figures and tables were left as loose text. Run: pip install pymupdf')
        return front
    import pymupdf
    imgdir.mkdir(exist_ok=True)

    def shot(page, rect, name):
        clip = (pymupdf.Rect(rect) + (-3, -3, 3, 3)) & page.rect
        z = min(4, 1400 / max(1, clip.width))
        page.get_pixmap(matrix=pymupdf.Matrix(z, z), clip=clip, alpha=False).save(str(imgdir / name))
        return name

    seen, done, miss = set(), collections.Counter(), []
    for n, (ph, blocks) in enumerate(pages[:len(doc)]):
        page, caps = doc[n], []
        pw = page.rect.width
        for b in blocks:
            for j, l in enumerate(b):
                m = CAPSTART.match(l['t'])
                if not m or l.get('drop') or (m.group(1)[0], m.group(2)) in seen:
                    continue
                if j and abs(b[j - 1]['h'] - l['h']) < .3 and b[j - 1]['x1'] > l['x1'] - 3 * bh:
                    continue                            # a body line that merely starts with "Figure 2: ..."
                ls, bare = [l], not m.group(0).rstrip()[-1:] in ':.' and m.end() >= len(l['t'].rstrip())
                for o in b[j + 1:j + 14]:
                    if (abs(o['h'] - l['h']) > .3 and not (bare and len(ls) == 1)) or not -1 < o['y0'] - ls[-1]['y1'] < .8 * l['h'] or CAPSTART.match(o['t']):
                        break
                    ls.append(o)
                seen.add((m.group(1)[0], m.group(2)))
                caps.append(dict(tab=m.group(1) == 'Table', n=m.group(2), ls=ls, r=pymupdf.Rect(box(ls)),
                                 t=join(x['t'] for x in ls).replace('](', '] (')))
        flat, gfx, tabs, rules, used = live(blocks), None, None, None, set()
        for c in caps:
            r, name = c['r'], ('Table ' if c['tab'] else 'Figure ') + c['n']
            if c['tab']:
                if tabs is None:
                    try:
                        with contextlib.redirect_stdout(io.StringIO()):
                            tabs = page.find_tables().tables
                    except Exception:
                        tabs = []
                dist = lambda t: min(abs(t.bbox[1] - r.y1), abs(r.y0 - t.bbox[3]))
                t = min((t for t in tabs if id(t) not in used and t.row_count > 1 and t.col_count > 1
                         and dist(t) < 45 and t.bbox[0] < r.x1 and t.bbox[2] > r.x0), key=dist, default=None)
                words, tb, grid = [w for l in flat for w in l.get('w', ())], None, None
                inside = lambda w, q: q[0] <= (w[0] + w[2]) / 2 <= q[2] and q[1] <= (w[1] + w[3]) / 2 <= q[3]

                def lines(q):                           # the words inside a rectangle, as rows of words
                    rows = []
                    for w in sorted((w for w in words if inside(w, q)), key=lambda w: w[1]):
                        if rows and abs(w[1] - rows[-1][0][1]) < .5 * (w[3] - w[1]):
                            rows[-1].append(w)
                        else:
                            rows.append([w])
                    return [sorted(row) for row in rows]

                if t is None:                           # no grid lines: a table set off by horizontal rules only
                    if rules is None:
                        rules = [g for g in (pymupdf.Rect(d['rect']) for d in page.get_cdrawings()) if g.height < 2 and g.width > 30]
                    near = [g for g in rules if g.x0 < r.x1 and g.x1 > r.x0 and min(abs(g.y0 - r.y1), abs(r.y0 - g.y1)) < 45]
                    g0 = min(near, key=lambda g: min(abs(g.y0 - r.y1), abs(r.y0 - g.y1)), default=None)
                    same = sorted((g for g in rules if g0 and abs(g.x0 - g0.x0) < 3 and abs(g.x1 - g0.x1) < 3), key=lambda g: g.y0)
                    if len(same) >= 2:
                        tb = pymupdf.Rect(g0.x0 - 2, same[0].y0, g0.x1 + 2, same[-1].y1)
                        rows, cols = lines(tb), []
                        for w in sorted(w for row in rows for w in row):          # columns = gaps no row's words cross
                            if cols and w[0] - cols[-1][1] < .6 * (w[3] - w[1]):
                                cols[-1][1] = max(cols[-1][1], w[2])
                            else:
                                cols.append([w[0], w[2]])
                        grid = [[' '.join(w[4] for w in row if c[0] - 1 <= (w[0] + w[2]) / 2 <= c[1] + 1).replace('|', '/') for c in cols]
                                for row in rows]
                        ncol = len(cols)
                    if not grid or ncol < 2 or len(grid) < 2:
                        miss.append(name)
                        continue
                else:
                    used.add(id(t))

                    cell = lambda cb: None if cb is None else join(' '.join(w[4] for w in row) for row in lines(cb)).replace('|', '/').strip(SOFT)
                    grid, tb, ncol = [[cell(cb) for cb in row.cells] for row in t.rows], pymupdf.Rect(t.bbox), t.col_count
                for l in flat:
                    if tb.contains(pymupdf.Point((l['x0'] + l['x1']) / 2, (l['y0'] + l['y1']) / 2)):
                        l['drop'] = True
                c['ls'] = [l for l in c['ls'] if not l.get('drop')] or c['ls'][:1]     # the caption stops where the table starts
                c['t'] = join(x['t'] for x in c['ls']).replace('](', '] (')
                widest = [max((len(w) for row in grid for w in (row[k] or '').split()), default=1) for k in range(ncol)]
                if ncol <= 4 and sum(widest) + 3 * ncol <= 40:          # fits the Palma's column as a real table
                    out = [['p', c['t']], ['tbl', [[x or '' for x in row] for row in grid]]]
                else:                                   # too wide for the column: a picture of the table, nothing else
                    out = [['fig', c['t'], shot(page, tb, f'{tag}tab-{c["n"]}.png')]]
            else:
                if gfx is None:
                    gfx = [pymupdf.Rect(i['bbox']) for i in page.get_image_info()] + [pymupdf.Rect(d['rect']) for d in page.get_cdrawings()]
                    gfx = [g for g in gfx if g.width * g.height < .85 * pw * ph and max(g.width, g.height) > 2 and g.y0 > -1 and g.y1 < ph + 1]
                mid = pw / 2                            # the figure sits above its caption, in the caption's column(s)
                sx0, sx1 = (0, pw) if r.x0 < mid - 10 and r.x1 > mid + 10 else (0, mid + 2) if (r.x0 + r.x1) / 2 < mid else (mid - 2, pw)
                floor = max((o['r'].y1 for o in caps if o is not c and o['r'].y1 <= r.y0 and o['r'].x0 < sx1 and o['r'].x1 > sx0), default=0)
                reg, top = None, r.y0
                for g in sorted((g for g in gfx if g.x0 >= sx0 - 3 and g.x1 <= sx1 + 3 and g.y1 <= r.y0 + 3 and g.y0 >= floor - 2),
                                key=lambda g: -g.y1):
                    if top - g.y1 > 22:
                        break
                    reg, top = (pymupdf.Rect(g) if reg is None else reg | g), min(top, g.y0)
                if reg is None or reg.width < 40 or reg.height < 25:
                    miss.append(name)
                    continue
                for l in flat:                          # labels typed inside the figure belong to the picture
                    if (sx0 <= (l['x0'] + l['x1']) / 2 <= sx1 and reg.y0 - 6 <= (l['y0'] + l['y1']) / 2 <= r.y0
                            and not any(l is x for x in c['ls'])):
                        reg, l['drop'] = reg | pymupdf.Rect(box([l])), True
                out = [['fig', c['t'], shot(page, reg, f'{tag}fig-{c["n"]}.png')]]
            done[c['tab']] += 1
            for l in c['ls']:
                l['drop'] = True
            c['ls'][0]['emit'] = out
    INFO.append(f'PAPER  {done[False]} figures and {done[True]} tables placed'
                + (f'; left as plain text: {", ".join(miss)}' if miss else ''))
    return front


def columns(pages):
    """Two-column pages: read the left column, then the right, between any full-width blocks."""
    xs = [x for _, bs in pages for b in bs for l in b for x in (l['x0'], l['x1'])]
    mid = (min(xs) + max(xs)) / 2 if xs else 0
    for n, (ph, blocks) in enumerate(pages):
        side = lambda l: 'F' if l['x0'] < mid - 6 and l['x1'] > mid + 6 else 'L' if l['x0'] + l['x1'] < 2 * mid else 'R'
        kinds = [side(l) for b in blocks for l in b if not l.get('drop')]
        if min(kinds.count('L'), kinds.count('R')) < 5 or kinds.count('F') > .2 * len(kinds):
            continue                                    # not a two-column page

        def halves(l):                                  # one "line" that is really a line from each column
            ws = l.get('w') or []
            gaps = sorted(b[0] - a[2] for a, b in zip(ws, ws[1:]))
            for k in range(len(ws) - 1):                # the gutter: the widest gap in the line, and it sits on the middle
                gap = ws[k + 1][0] - ws[k][2]
                if ws[k][2] <= mid + 2 and ws[k + 1][0] >= mid - 2 and gap == gaps[-1] and gap > max(.6 * l['h'], 2 * gaps[len(gaps) // 2]):
                    cut = lambda p, **kw: dict(l, x0=p[0][0], x1=p[-1][2], t=' '.join(w[4] for w in p), w=p, **kw)
                    return [cut(ws[:k + 1]), cut(ws[k + 1:], emit=())]
            return [l]

        info = []
        for b in blocks:
            b = [h for l in b for h in halves(l)]
            parts = [[l for l in b if side(l) == k] for k in 'LR']
            for ls in (parts if all(parts) and len(parts[0]) + len(parts[1]) == len(b) else [b]):   # pdftotext mixed the columns
                live = [l for l in ls if not l.get('drop')] or ls
                x0, x1 = min(l['x0'] for l in live), max(l['x1'] for l in live)
                info.append((min(l['y0'] for l in live), x0, 'F' if x0 < mid - 6 and x1 > mid + 6 else 'L' if x0 + x1 < 2 * mid else 'R', ls))
        out, band = [], []
        for i in sorted(info, key=lambda i: i[:2]) + [(0, 0, 'F', None)]:
            if i[2] == 'F':
                out += [x[3] for x in band if x[2] == 'L'] + [x[3] for x in band if x[2] == 'R'] + [i[3]]
                band = []
            else:
                band.append(i)
        pages[n] = (ph, out[:-1])


def pdf_blocks(path, a, tmp, tag=''):
    try:                                                # pymupdf adds letter repair, figures and tables; all optional
        try:
            import pymupdf
        except ImportError:
            import fitz as pymupdf
        doc = pymupdf.open(str(path))
        pages = pdf_pages(path, glyph_fixes(doc))
    except Exception:
        doc, pages = None, pdf_pages(path)
    count = lambda: sum(len(l['t']) for _, bs in pages for b in bs for l in b)
    ocred = count() < 80 * max(1, len(pages))
    if ocred:
        pages = ocr_pages(path, tmp, a.ocr_lang)
        if count() < 20 * max(1, len(pages)):
            sys.exit('No readable text found. Scans need poppler-utils and tesseract-ocr installed, plus the right --ocr-lang.')
    every = [l for _, bs in pages for b in bs for l in b]

    # body text size = line height at the middle of all characters
    hs, half, acc = sorted((l['h'], len(l['t'])) for l in every), sum(len(l['t']) for l in every) / 2, 0
    for bh, n in hs:
        acc += n
        if acc >= half:
            break
    big_at = 1.35 if ocred else 1.18

    # running headers, footers, page numbers: margin text that repeats, is a bare number,
    # or sits alone at the same height on many pages (catches headers that change per chapter)
    def band(top):
        ys = []
        for _, bs in pages:
            ls = sorted((l for b in bs for l in b), key=lambda l: l['y0'])
            if len(ls) > 1:
                l, nb = (ls[0], ls[1]) if top else (ls[-1], ls[-2])
                if (nb['y0'] - l['y1'] if top else l['y0'] - nb['y1']) > .8 * bh:
                    ys.append(l['y0'])
        hits = lambda y: sum(abs(y - z) < .6 * bh for z in ys)
        best = max(set(ys), key=hits, default=None)
        return best if ys and hits(best) >= max(3, .3 * len(pages)) else None

    bands = [y for y in (band(True), band(False)) if y is not None]
    key = lambda t: re.sub(r'\d+', '#', t.lower()).strip()
    pnum = re.compile(r'(?i)^\W*(page\s*)?(\d{1,4}|[ivxlc]{1,7})(\s*(of|/)\s*\d+)?\W*$')
    edge = lambda p: [l for b in p[1] for l in b if l['y1'] < .1 * p[0] or l['y0'] > .9 * p[0]]
    seen = collections.Counter(k for p in pages for k in {key(l['t']) for l in edge(p)})
    need = 3 if len(pages) > 6 else 2
    for p in pages:
        for l in edge(p):
            if l['h'] < big_at * bh and (pnum.match(l['t']) or any(abs(l['y0'] - y) < .6 * bh for y in bands)
                                        or (len(pages) > 2 and seen[key(l['t'])] >= need)):
                l['drop'] = True
    gone = collections.Counter(re.sub(r'\d+', '#', l['t']) for l in every if l.get('drop'))
    INFO.append(f'PAGES  {len(pages)}{" (scanned, OCRed)" if ocred else ""}, {sum(len(l["t"].split()) for l in every):,} words on them. '
                f'Removed as headers/footers: ' + ('; '.join(f'"{t[:40]}" x{n}' for t, n in gone.most_common(4)) or 'nothing'))

    # bookmarks, when the PDF has them, are the most reliable chapter list
    norm = lambda s: re.sub(r'\W+', '', s.lower())
    marks = {}
    try:
        from pypdf import PdfReader
        rd = PdfReader(str(path))
        junk = r'(?i)^\W*(microsoft word|untitled|anonymous|unknown|user|admin|author|owner|scan|document|doc|img)[\W\d_]*\b|\.(docx?|pdf|indd|tex|dvi|pages)$'
        for k, v in (('title', rd.metadata.title), ('author', rd.metadata.author)) if rd.metadata else ():
            if v and len(v.strip()) > 2 and not re.search(junk, v.strip()):
                META.setdefault(k, v.strip())
        for it in rd.outline:
            if not isinstance(it, list):
                marks.setdefault(rd.get_destination_page_number(it), []).append(norm(it.title))
    except Exception:
        pass

    front = a.paper and not ocred and paper_layout(doc, pages, a, tmp / 'img', tag, bh)
    if a.paper and not ocred:
        columns(pages)
    multi = [b for _, bs in pages for b in bs if len(b) > 2]
    inner = [(l, b) for b in multi for l in b[:-1]]
    justified = sum(max(x['x1'] for x in b) - l['x1'] < .5 * bh for l, b in inner) > .6 * max(1, len(inner))
    n_ind = sum(.6 * bh < l['x0'] - min(x['x0'] for x in b) < 8 * bh for b in multi for l in b)
    indented = .04 < n_ind / max(1, sum(map(len, multi))) < .5      # paragraphs start with a first-line indent
    hre, out, pend, drop = re.compile(a.heading_regex or HEAD), [], [], ''

    def flush():
        out.extend(pend)
        pend.clear()

    def emit(par, cont, small):
        """cont: 0 = new paragraph, 1 = may continue the previous one, 2 = certainly continues it."""
        nonlocal drop
        t, drop = drop + join(par, a.cjk), ''
        if not t:
            return
        if small:                                       # footnote: hold it until the paragraph above is finished
            pend.append(['p', t])
            return
        lp = out[-1] if out and out[-1][0] == 'p' else None
        if lp and (cont == 2 or (cont == 1 and (re.search(r'\w-$', lp[1]) or
                                                (not re.search(END, lp[1]) and re.match(LOW, t))))):
            lp[1] = join([lp[1], t], a.cjk)             # paragraph runs on across a page or column break
        else:
            flush()
            out.append(['p', t])

    for i, (ph, blocks) in enumerate(pages):
        ptop = True                                     # nothing but headers/footnotes emitted on this page yet
        for b in blocks:
            for l in b:
                pend.extend(l.get('emit', ()))           # a figure or table waits until the paragraph above is finished
            ls = [l for l in b if not l.get('drop')]
            if not ls:
                continue
            left, right = min(l['x0'] for l in ls), max(l['x1'] for l in ls)
            small = ls[0]['y0'] > .55 * ph and all(l['h'] <= .9 * bh for l in ls)
            ind = [.6 * bh < l['x0'] - left < 8 * bh for l in ls]
            use_ind = sum(ind) <= len(ls) / 2           # not a hanging-indent or fully indented block
            short = (lambda l: l['x1'] < right - 1.5 * bh) if justified else (lambda l: l['x1'] < right - .25 * (right - left))
            hd = [0] * len(ls)                          # 1 = line starts a numbered heading, 2 = continues one
            for j, l in enumerate(ls if a.paper or a.numbered_headings else ()):
                odd = abs(l['h'] - bh) > .06 * bh and len(l['t']) <= 90      # set in a different face from the body
                if odd and NUMH.match(l['t']) and not re.match(r'\d+\.\d+\.\d', l['t']):
                    hd[j] = 1
                elif (odd and j and hd[j - 1] and (j < 2 or hd[j - 2] != 2) and abs(l['h'] - ls[j - 1]['h']) < .2
                      and not re.search(r'[.!?]$', ls[j - 1]['t'])):
                    hd[j] = 2
            runs = []                                   # consecutive lines of the same size class
            for j, l in enumerate(ls):
                big = round(l['h'] / bh * 5) / 5 if l['h'] >= big_at * bh else 0
                if runs and runs[-1][0] == big:
                    runs[-1][1].append(j)
                else:
                    runs.append([big, [j]])
            first = True
            for big, idx in runs:
                txt = join([ls[j]['t'] for j in idx], a.cjk)
                nt = norm(txt)
                mk = next((m for m in marks.get(i, []) if nt and min(len(nt), len(m)) >= 4 and (nt in m or m in nt)
                           and len(nt) <= 2 * len(m) + 10), None)
                rx = len(idx) == 1 and len(txt) <= 90 and hre.search(txt)
                wordy = sum(c.isalpha() for c in txt) >= 3 or re.fullmatch(r'[\d.]+', txt)   # not a stray glyph or axis label
                if len(idx) <= 3 and len(txt) <= 150 and wordy and (big or mk or rx):
                    if big >= 1.8 and len(txt) == 1 and txt.isalpha():   # drop cap: glue it to the next paragraph
                        drop = txt
                        continue
                    if mk:
                        marks[i].remove(mk)
                    flush()
                    out.append(['h', 1 if (mk or rx) else 0, txt, big, i])
                    first = ptop = False
                    continue
                # top of a page, first line not indented in a book that indents paragraphs = same paragraph as before
                cont = 2 if (first and ptop and indented and use_ind and len(ls) > 1 and not ind[idx[0]]) else int(first)
                cont, par = (0 if hd[idx[0]] == 1 else cont), []
                for n, j in enumerate(idx):
                    prev, k = (ls[idx[n - 1]] if n else None), idx[n - 1]
                    item = ITEM.match(ls[j]['t']) and (ls[j]['t'][0] == '•' or (not use_ind and not ind[j]))
                    if prev and (hd[j] == 1 or (hd[k] and not hd[j]) or item or (hd[j] != 2 and (
                            (use_ind and ind[j] and not ind[k]) or (short(prev) and re.search(END, prev['t']))))):
                        emit(par, cont, small)
                        par, cont = [], 0
                    par.append(ls[j]['t'])
                emit(par, cont, small)
                first, ptop = False, ptop and small
    flush()

    # heading levels from type size: biggest recurring size = chapter, next = section
    sized = lambda: [b for b in out if b[0] == 'h' and b[1] == 0]
    tiers = sorted({b[3] for b in sized()}, reverse=True)
    top = [b for b in sized() if b[3] == tiers[0]] if tiers else []
    if not front and len(top) == 1 and top[0][4] <= 1:  # one huge line on page 1-2 is the title
        META['title'] = top[0][2]                       # more trustworthy than the PDF's metadata field
        t0 = next(n for n, b in enumerate(out) if b is top[0])
        t1 = next((n for n in range(t0, len(out)) if out[n][0] == 'p' and len(out[n][1].split()) >= 40 and re.search(r'[.!?]$', out[n][1])), len(out))
        later = {b[3] for b in out[t1:] if b[0] == 'h'}
        for b in out[t0 + 1:t1]:                        # byline / affiliation sizes that never recur are not sections
            if b[0] == 'h' and b[1] == 0 and b[3] not in later:
                b[:] = ['p', b[2]]
        top[0][:] = ['skip']
    tiers = sorted({b[3] for b in sized()}, reverse=True)
    forced = [b[3] for b in out if b[0] == 'h' and b[1] == 1]
    anchor = max(set(x for x in forced if x), key=forced.count, default=0)   # type size of the known chapter headings
    for b in out:
        if b[0] == 'h':
            lvl = b[1] or (1 if anchor and b[3] >= anchor else
                           min(3, (2 if forced else 1) + [t for t in tiers if not anchor or t < anchor].index(b[3])))
            b[:] = ['h', lvl, b[2]]
    return [b for b in out if b[0] != 'skip']


# ---------- blocks <-> clean text ----------
def tidy(blocks, a):
    out, skip = [], False
    if a.numbered_headings or a.paper:                  # "5.1.1 Title. The text runs on..." : split heading from text
        split = []
        for b in blocks:
            m = b[0] == 'p' and re.match(r'(\d{1,2}\.\d{1,2}\.\d{1,2})\.?\s+[^\W\d_a-z][^.!?]{2,140}[.!?]?'
                                         r'(\s+(?=[^\W\d_a-z“"(\[])|$)', b[1])
            split += [['h', 3, b[1][:m.end()].strip()]] + ([['p', b[1][m.end():]]] if b[1][m.end():] else []) if m else [b]
        blocks = split
    for n, b in enumerate(blocks):
        if skip:
            skip = False
            continue
        nxt = blocks[n + 1] if n + 1 < len(blocks) else ['']
        if (a.numbered_headings or a.paper) and b[0] == 'p':
            m = re.fullmatch(r'(\d{1,2}(?:\.\d{1,2}){0,2})\.?\s+[^\W\d_a-z][^.!?]{2,90}', b[1])
            lone = re.fullmatch(r'\d{1,2}(\.\d{1,2}){1,2}\.?', b[1])     # "3.1" on its own line, title on the next
            if m:
                b = ['h', min(3, m.group(1).count('.') + 1), b[1]]
            elif lone and nxt[0] == 'p' and len(nxt[1]) <= 80 and not re.search(r'[.!?,;:]$', nxt[1]):
                b, skip = ['h', min(3, b[1].strip('.').count('.') + 1), b[1] + ' ' + nxt[1]], True
            elif a.paper and re.fullmatch(r'(?i)(author )?keywords|ccs concepts|index terms|general terms', b[1].rstrip('.:')):
                b = ['h', 2, b[1]]
        prev = out[-1] if out and out[-1][0] == 'h' else None
        if b[0] == 'h' and prev and re.fullmatch(r'[\d.]+', prev[2]):      # "2." and "Overview" set as two pieces
            prev[1:] = [b[1], prev[2] + ' ' + b[2]]
        elif (b[0] == 'h' and prev and (prev[1] == b[1] or (prev[1] == 1 and LABEL.match(prev[2])))
              and not (NUMH.match(prev[2]) and NUMH.match(b[2]))):
            prev[2] = prev[2].rstrip(' .:') + ': ' + b[2]                   # "CHAPTER ONE" + "The Title"
        else:
            out.append(b)
    if a.paper:
        # figures and tables leave runs of short label lines, and cut the paragraph around them in two
        junk = lambda b: b[0] == 'p' and len(b[1].split()) <= 6 and not re.search(END, b[1]) and not CAP.match(b[1])
        n = next((k for k, b in enumerate(out) if b[0] == 'h'), len(out))      # leave the byline alone
        keep, dropped = out[:n], 0
        while n < len(out):
            m = n
            while m < len(out) and junk(out[m]):
                m += 1
            if m - n >= 3:
                dropped, n = dropped + m - n, m
            else:
                keep.append(out[n])
                n += 1
        out, last = [], None
        for b in keep:
            if b[0] in ('fig', 'tbl', 'rec'):           # a figure or table does not end the paragraph around it
                out.append(b)
                continue
            if b[0] == 'p' and last and re.match(LOW, b[1]) and not CAP.match(b[1]):
                last[1] = join([last[1], b[1]], a.cjk)              # second half of a paragraph a figure interrupted
                last = last if not re.search(END, last[1]) else None
                continue
            out.append(b)
            if not (b[0] == 'p' and CAP.match(b[1])):               # a caption does not end the paragraph before it
                last = b if b[0] == 'p' and not re.search(END, b[1]) else None
        k = max((n for n, b in enumerate(out) if b[0] == 'h' and REFH.match(b[2])), default=None)
        if k is not None:                               # one paragraph per numbered reference
            e = next((n for n in range(k + 1, len(out)) if out[n][0] != 'p'), len(out))
            txt, cuts = join(b[1] for b in out[k + 1:e]), []
            for m in re.finditer(r'\[(\d{1,3})\]\s', txt):
                if int(m.group(1)) == len(cuts) + 1:
                    cuts.append(m.start())
            if len(cuts) >= 3 and cuts[0] == 0:
                out[k + 1:e] = [['p', txt[x:y].strip()] for x, y in zip(cuts, cuts[1:] + [len(txt)])]
        if dropped:
            INFO.append(f'PAPER  dropped {dropped} stray label lines from figures/tables/listings')
    if a.split and not any(b[0] == 'h' for b in out):                # no chapters at all: neutral breaks
        res, n, k = [], 0, 0
        for b in out:
            if n == 0:
                k += 1
                res.append(['h', 1, str(k)])
            res.append(b)
            n += len(str(b[1:]).split())
            if n >= a.split:
                n = 0
        out = res
    return out


def strip_front(blocks, title, author):
    """Drop title / "by Author" lines before the first chapter: the EPUB gets its own title page."""
    norm = lambda s: re.sub(r'\W+', '', s.lower())
    t, au = norm(title), norm(author)
    dup = {t, au, 'by' + au, t + 'by' + au} - {'', 'by'}
    first = next((i for i, b in enumerate(blocks) if b[0] == 'h' and b[1] == 1 and norm(b[2]) not in dup), len(blocks))
    return [b for i, b in enumerate(blocks) if not (i < min(first, 15) and b[0] in ('h', 'p') and norm(b[-1]) in dup)]


def dump(blocks, img=''):
    o = []
    for b in blocks:
        if b[0] == 'h':
            o.append('#' * b[1] + ' ' + b[2])
        elif b[0] == 'hr':
            o.append('* * *')
        elif b[0] in ('v', 'rec'):
            o.append('\n'.join(b[1]))
        elif b[0] == 'fig':
            o.append(f'![{b[1]}]({img}{b[2]})')
        elif b[0] == 'tbl':
            o.append('\n'.join('| ' + ' | '.join(r) + ' |' for r in b[1]))
        else:
            o.append(re.sub(r'^(#|!\[|\* \* \*$)', r'\\\1', b[1]))   # body text that only looks like markup
    return ('\n\n'.join(o) + '\n').replace(SOFT, '')


def to_html(text, base):
    def esc(s):
        s = html.escape(re.sub(r'(?<=[^\s-])\s?--\s?(?=[^\s-])', '—', re.sub(r'^\\(?=#|\*|!)', '', s)), quote=False)
        s = re.sub(r'(?<![\w/.])_(\S(?:[^_]{0,200}\S)?)_(?![\w/])', r'<em>\1</em>', s)
        return re.sub(r'https?://[^\s<"]*[^\s<".,;:)]', lambda m: f'<a href="{m.group(0)}">{m.group(0)}</a>', s)

    blks = [[l.strip() for l in blk.split('\n') if l.strip()] for blk in re.split(r'\n\s*\n', text.strip())]
    refs, inref = set(), False                          # numbered reference list: "[12]" in the text links to its entry
    for ls in blks:
        if ls and ls[0].startswith('#'):
            inref = bool(REFH.match(ls[0].lstrip('# ')))
        elif inref and len(ls) == 1 and re.match(r'\[\d{1,3}\]\s', ls[0]):
            refs.add(ls[0][1:ls[0].index(']')])
    refs = refs if len(refs) >= 3 else set()
    num = lambda d: f'<a class="cite" href="#ref-{d.group(0)}">{d.group(0)}</a>' if d.group(0) in refs else d.group(0)
    cite = lambda s: re.sub(r'\[\d{1,3}(?:\s*[,–-]\s*\d{1,3})*\]', lambda m: re.sub(r'\d+', num, m.group(0)), s) if refs else s
    o, inref = [], False
    for ls in blks:
        if not ls:
            continue
        m, img = re.match(r'(#{1,3})\s+(.*)', ls[0]), re.fullmatch(r'!\[(.*?)\]\((.+?)\)', ls[0])
        if len(ls) == 1 and m:
            inref = bool(REFH.match(m.group(2)))
            o.append(f'<h{len(m.group(1))}>{esc(m.group(2))}</h{len(m.group(1))}>')
        elif len(ls) == 1 and img:                      # ![caption](picture.png), path relative to the text file
            o.append(f'<figure><img src="{html.escape(str((base / img.group(2)).resolve()))}" alt="{html.escape(img.group(1))}"/>'
                     + (f'<figcaption>{esc(img.group(1))}</figcaption>' if img.group(1) else '') + '</figure>')
        elif ls == ['* * *']:
            o.append('<hr/>')
        elif len(ls) > 1 and all(l.startswith('|') for l in ls):          # | a | b | rows: a table, first row is the header
            rows = [[c.strip() for c in l.strip('|').split('|')] for l in ls]
            o.append('<table>' + ''.join('<tr>' + ''.join(f'<{t}>{cite(esc(c))}</{t}>' for c in r for t in ['td' if n else 'th'])
                                         + '</tr>' for n, r in enumerate(rows)) + '</table>')
        elif len(ls) > 1 and all(re.match(r'_[^_]+:_ ', l) for l in ls):  # one table row written out as "Column: value" lines
            o.append('<div class="rec">' + ''.join(f'<p>{esc(l)}</p>' for l in ls) + '</div>')
        elif len(ls) > 1:
            o.append('<div class="verse">' + '<br/>'.join(map(esc, ls)) + '</div>')
        elif inref and re.match(r'\[\d{1,3}\]\s', ls[0]) and ls[0][1:ls[0].index(']')] in refs:
            n = ls[0][1:ls[0].index(']')]
            o.append(f'<div class="ref"><p><span id="ref-{n}">[{n}]</span> {esc(ls[0][len(n) + 2:].strip())}</p></div>')
        elif ITEM.match(ls[0]) or CAPSTART.match(ls[0]):               # list item, or the caption of a rebuilt table
            o.append(f'<div class="{"li" if ITEM.match(ls[0]) else "cap"}"><p>{cite(esc(ls[0]))}</p></div>')
        else:
            o.append(f'<p>{cite(esc(ls[0]))}</p>')
    return '<html><body>\n' + '\n'.join(o) + '\n</body></html>'


# ---------- cover + EPUB ----------
def cover(title, author, lang, path):
    from PIL import Image, ImageDraw, ImageFont
    W, H, M = 1200, 1800, 110

    def font(style, size):
        try:
            return ImageFont.truetype(run('fc-match', '-f', '%{file}', f'serif:{style}:lang={lang}').stdout.decode(), size)
        except Exception:
            return ImageFont.load_default(size)

    def wrap(d, s, f):
        lines, cur = [], ''
        for w in s.split():
            while d.textlength(w, font=f) > W - 2 * M and len(w) > 1:      # unbreakable run (CJK, URLs)
                k = next(n for n in range(len(w), 0, -1) if d.textlength(w[:n], font=f) <= W - 2 * M or n == 1)
                if cur:
                    lines.append(cur)
                    cur = ''
                lines.append(w[:k])
                w = w[k:]
            if cur and d.textlength(cur + ' ' + w, font=f) > W - 2 * M:
                lines.append(cur)
                cur = w
            else:
                cur = (cur + ' ' + w).strip()
        return lines + ([cur] if cur else [])

    im = Image.new('L', (W, H), 255)
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, W - 1, H - 1], outline=0, width=10)   # frame, so a white cover reads on a white shelf
    d.rectangle([M, 150, M + 180, 166], fill=0)
    size = 150
    while True:
        f = font('bold', size)
        lines = wrap(d, title, f)
        whole = all(d.textlength(w, font=f) <= W - 2 * M for w in title.split() if len(w) <= 14)
        if size <= 50 or (whole and len(lines) * size * 1.15 <= H * .55):
            break
        size -= 10
    y = 230
    for l in lines:
        d.text((M, y), l, font=f, fill=0)
        y += int(size * 1.15)
    if author:
        fa = font('regular', 60)
        al = wrap(d, author, fa)
        for n, l in enumerate(al):
            d.text((M, H - M - 75 * (len(al) - n)), l, font=fa, fill=0)
    im.save(path, quality=90)


def pandoc_bin():
    if shutil.which('pandoc'):
        return 'pandoc'
    try:
        import pypandoc
        return pypandoc.get_pandoc_path()
    except Exception:
        sys.exit('pandoc is missing. Run: pip install pypandoc_binary   (add --break-system-packages if pip asks)')


def build(src, fmt, a, out, tmp, extra=()):
    (tmp / 'palma.css').write_text(CSS)
    cmd = [pandoc_bin(), src, '-f', fmt, '-t', 'epub3', '-o', out, '--css', tmp / 'palma.css', '--toc-depth=2',
           '--metadata', 'title=' + a.title, '--metadata', 'lang=' + a.lang, *extra]
    if a.author:
        cmd += ['--metadata', 'author=' + a.author]
    try:
        cover(a.title, a.author, a.lang, tmp / 'cover.jpg')
        cmd += ['--epub-cover-image', tmp / 'cover.jpg']
    except Exception as e:
        WARN.append(f'No cover image ({e}). pip install pillow to get one.')
    r = run(*cmd)
    if r.returncode and fmt.endswith('+smart'):             # older pandoc: no smart quotes for this format
        cmd[3] = fmt[:-6]
        r = run(*cmd)
    if r.returncode:
        sys.exit('pandoc failed: ' + r.stderr.decode()[-600:])


def report(out, text=None):
    z = zipfile.ZipFile(out)
    names = z.namelist()
    bad = [] if names and names[0] == 'mimetype' else ['mimetype is not the first entry']
    for n in names:
        if n.endswith(('.xhtml', '.opf', '.ncx')):
            try:
                xml.dom.minidom.parseString(z.read(n))
            except Exception as e:
                bad.append(f'{n}: {e}')
    nav = next((z.read(n).decode() for n in names if n.endswith('nav.xhtml')), '')
    nav = (re.search(r'<nav[^>]*epub:type="toc".*?</nav>', nav, re.S) or re.search('.*', nav, re.S)).group(0)
    toc = [html.unescape(re.sub(r'<[^>]+>', '', t)).strip() for t in re.findall(r'<a [^>]*>(.*?)</a>', nav, re.S)]
    words = sum(len(re.sub(rb'<[^>]+>', b' ', z.read(n)).split()) for n in names if re.search(r'ch\d+\.xhtml$', n))
    print(f'EPUB   {out}  ({pathlib.Path(out).stat().st_size / 1024:.0f} KB, about {words:,} words)')
    for i in INFO:
        print(i)
    show = toc if len(toc) <= 30 else toc[:20] + [f'... {len(toc) - 23} more ...'] + toc[-3:]
    print(f'TOC    {len(toc)} entries' + ''.join(f'\n         {t[:70]}' for t in show))
    if text is not None:
        paras, start = [], None                         # (first line number, text) of every body paragraph
        for n, l in enumerate(text.split('\n') + [''], 1):
            if l.strip() and start is None:
                start, buf = n, []
            if l.strip():
                buf.append(l.strip())
            elif start is not None:
                if not buf[0].startswith(('#', '![', '|', '_')) and buf != ['* * *']:
                    paras.append((start, ' '.join(buf)))
                start = None
        avg = sum(len(p.split()) for _, p in paras) / max(1, len(paras))
        print(f'TEXT   {len(paras):,} paragraphs, average {avg:.0f} words')
        for tag, k in (('START ', 0), ('MIDDLE', len(paras) // 2), ('END   ', len(paras) - 1)) if paras else ():
            print(f'{tag} line {paras[k][0]}: {paras[k][1][:150]}')
        first_h = next((n for n, l in enumerate(text.split('\n'), 1) if l.startswith('#')), 0)
        stray = [n for n, p in paras if n > first_h and len(p.split()) <= 6 and not re.search(END, p) and not CAP.match(p)]
        cut = [n for n, p in paras if re.match(LOW, p)]
        at = lambda ns: ', '.join(map(str, ns[:6])) + (' ...' if len(ns) > 6 else '')
        lost = [m.group(0) for m in BROKEN.finditer(text)]
        if len(lost) >= 3:
            WARN.append(f'About {len(lost)} words are missing the letters fi, fl or ff ("{lost[0]}", "{lost[1]}"): this PDF has a '
                        'broken font map. Run pip install pymupdf and convert again; the script then restores them.')
        if avg > 250:
            WARN.append('Paragraphs are very long: breaks were probably lost.')
        if avg < 12 and len(paras) > 50:
            WARN.append('Paragraphs are very short: lines may not have been rejoined.')
        if len(stray) >= 12 and len(stray) > .05 * len(paras):
            WARN.append(f'{len(stray)} short stray lines (figure or table labels, code, a contents page?) at lines {at(stray)}. For a paper, rerun with --paper.')
        if len(cut) >= 3:
            WARN.append(f'{len(cut)} paragraphs start mid-sentence (split by a figure, footnote or page break) at lines {at(cut)}.')
        seq = {}                                        # numbered headings: 6.2 followed by 6.4 means one was missed
        for n, l in enumerate(text.split('\n'), 1):
            m = re.match(r'#+ (\d+)(?:\.(\d+))?[. ]', l)
            if m:
                par, k = (m.group(1), int(m.group(2))) if m.group(2) else ('', int(m.group(1)))
                if k > seq.get(par, k) + 1:
                    WARN.append(f'Section numbering jumps to "{l[:40]}" at line {n}: a heading before it was missed (look for a lone number line).')
                seq[par] = k
        if words > 15000 and not re.search(r'(?m)^# ', text):
            WARN.append('No chapters detected in a long text. Find the real chapter pattern and pass --heading-regex, or use --split 6000.')
    for w in WARN + ['INVALID XML  ' + b for b in bad]:
        print('WARN  ', w)
    return not bad


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('inputs', nargs='+', help='.txt, .md or .pdf (several .txt/.pdf files become one book, in order)')
    ap.add_argument('-o', '--out', help='output .epub (default: "<Title> - <Author>.epub" in the current folder)')
    ap.add_argument('--title')
    ap.add_argument('--author')
    ap.add_argument('--lang', default='en', help='language code of the text, e.g. en, es, ko (drives hyphenation)')
    ap.add_argument('--ocr-lang', default='eng', help='tesseract language for scanned PDFs, e.g. eng, spa, kor')
    ap.add_argument('--heading-regex', help='regex that marks a chapter heading line, replacing the built-in one')
    ap.add_argument('--numbered-headings', action='store_true', help='treat "2.1 Related Work" style lines as headings')
    ap.add_argument('--paper', action='store_true', help='papers/reports (PDF): column order, numbered headings, figures, tables, linked references')
    ap.add_argument('--no-caps-headings', action='store_true', help='do not treat ALL-CAPS lines as headings (.txt)')
    ap.add_argument('--split', type=int, default=0, metavar='WORDS', help='text with no headings: break every N words')
    ap.add_argument('--from-text', action='store_true', help='input is an already-cleaned .txt from a previous run')
    a = ap.parse_args()
    a.cjk = a.lang[:2] in ('zh', 'ja')
    ins = [pathlib.Path(p) for p in a.inputs]
    for p in ins:
        if not p.exists():
            sys.exit(f'Not found: {p}')
    nice = lambda p: re.sub(r'[\s_]+', ' ', re.sub(r'(?<=\w)-(?=\w)', ' ', p.stem)).strip()
    md = [p for p in ins if p.suffix.lower() in ('.md', '.markdown')]
    if md and len(ins) > 1:
        sys.exit('Convert Markdown files one at a time, or concatenate them into one .md first.')

    with tempfile.TemporaryDirectory() as t:
        tmp, text, blocks, src = pathlib.Path(t), None, [], ins[0].read_text('utf-8', 'replace') if md else ''
        if md:
            fm = re.match(r'---\n(.*?)\n---\n', src, re.S)
            body = re.sub(r'(?ms)^(```|~~~).*?^\1\s*$', '', src[fm.end():] if fm else src)
            h1, h2 = re.findall(r'(?m)^# +(.+)$', body), re.findall(r'(?m)^## ', body)
            for k in ('title', 'author'):
                m = re.search(rf'(?m)^{k}:\s*["\']?(.+?)["\']?\s*$', fm.group(1)) if fm else None
                if m:
                    META.setdefault(k, m.group(1))
            extra = ['--resource-path', ins[0].resolve().parent, '--no-highlight']
            if len(h2) >= 2 and len(h1) <= 1:           # "# Title" + "## Sections": sections become the chapters
                extra.append('--shift-heading-level-by=-1')
                if h1:
                    META.setdefault('title', h1[0].strip('# '))
        elif a.from_text:
            text = '\n\n'.join(read_txt(p) for p in ins)
        else:
            for k, p in enumerate(ins):
                tag = f'{k + 1}-' if len(ins) > 1 else ''
                b = pdf_blocks(p, a, tmp, tag) if p.suffix.lower() == '.pdf' else txt_blocks(read_txt(p), a)
                if len(ins) > 1 and not any(x[0] == 'h' and x[1] == 1 for x in b):
                    b.insert(0, ['h', 1, nice(p)])
                blocks += b
        a.title = a.title or META.get('title') or nice(ins[0])
        a.author = a.author or META.get('author') or ''
        safe = re.sub(r'[\\/:*?"<>|]+', ' ', a.title + (' - ' + a.author if a.author else '')).strip(' .')[:120]
        out = pathlib.Path(a.out or safe + '.epub')
        if blocks:
            pics = sorted((tmp / 'img').glob('*.png')) if (tmp / 'img').is_dir() else []
            if pics:                                    # figure crops live beside the clean text, so a rebuild finds them
                (out.parent / (out.stem + '_img')).mkdir(exist_ok=True)
                for f in pics:
                    shutil.copy(f, out.parent / (out.stem + '_img') / f.name)
            text = dump(strip_front(tidy(blocks, a), a.title, a.author), out.stem + '_img/')
        if md:
            build(ins[0], 'markdown+smart', a, out, tmp, extra)
        else:
            if not a.from_text:
                clean_txt = out.parent / (out.stem + '.clean.txt')
                clean_txt.write_text(text, 'utf-8')
                print(f'TEXT   {clean_txt}  (edit it, then rerun on it with --from-text to rebuild)')
            (tmp / 'book.html').write_text(to_html(text, ins[0].resolve().parent if a.from_text else out.resolve().parent), 'utf-8')
            build(tmp / 'book.html', 'html+smart', a, out, tmp)
        print(f'TITLE  {a.title}\nAUTHOR {a.author or "(none)"}')
        sys.exit(0 if report(out, text) else 1)


if __name__ == '__main__':
    main()
