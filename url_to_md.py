#!/usr/bin/env python3
"""url_to_md.py: web article (URL or saved .html) -> clean Markdown + local images, ready for palma_epub.py."""
import argparse, hashlib, json, pathlib, re, sys, urllib.parse, urllib.request

import trafilatura

UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15'


def get(url, timeout=30):
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept-Language': 'en,*;q=0.5'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(), r.headers.get_content_charset() or 'utf-8', r.geturl()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('src', help='an http(s) URL, or a saved .html file')
    ap.add_argument('--url', help='the page\'s real address, when src is a saved file (fixes relative image links)')
    ap.add_argument('-o', '--out', required=True, help='output .md path; images go in <stem>_img/ beside it')
    ap.add_argument('--no-images', action='store_true')
    a = ap.parse_args()
    out = pathlib.Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    if re.match(r'https?://', a.src):
        try:
            raw, enc, base = get(a.src)
        except Exception as e:
            sys.exit(f'FETCH FAILED {a.src}: {e}. Use the fallbacks in the skill (her Mac, or the browser).')
    else:
        raw, enc, base = pathlib.Path(a.src).read_bytes(), 'utf-8', a.url or ''
    page = raw.decode(enc, 'replace')
    try:                                               # old sites lay pages out with tables: unwrap any table
        import lxml.html                                # whose cells hold whole paragraphs, keep real data tables
        tree = lxml.html.fromstring(page)
        for t in tree.iter('table'):
            if any(len(c.text_content().split()) > 120 for c in t.iter('td', 'th')):
                for el in [t, *t.iter('tbody', 'thead', 'tr', 'td', 'th')]:
                    el.tag = 'div'
        page = lxml.html.tostring(tree, encoding='unicode')
    except Exception:
        pass

    md = trafilatura.extract(page, url=base or None, output_format='markdown', include_images=not a.no_images,
                             include_links=False, include_tables=True, include_formatting=True, favor_recall=True)
    meta = trafilatura.extract_metadata(page, default_url=base or None)
    if not md or len(md.split()) < 150:
        sys.exit(f'THIN only {len(md.split()) if md else 0} words came out. Likely a paywall, a sign-in wall or a page '
                 'built by JavaScript. Use the browser fallback in the skill.')

    title = (meta.title if meta and meta.title else '').strip()
    site0 = (meta.sitename if meta and meta.sitename else '').strip()
    for sep in (' | ', ' - ', ' – ', ' — ', ' :: '):  # "E Ink - Wikipedia" -> "E Ink"
        if sep in title:
            head_, tail = title.rsplit(sep, 1)
            if len(tail.split()) <= 4 and len(head_) >= 3:
                title = head_.strip()
    author = (meta.author if meta and meta.author else '').strip()
    site = (meta.sitename if meta and meta.sitename else '').strip()
    date = (meta.date if meta and meta.date else '').strip()
    if date.endswith('-01-01'):                        # trafilatura's guess when only a year is known
        date = date[:4]
    lang = ''
    m = re.search(r'<html[^>]*\blang=["\']?([a-zA-Z]{2})', page[:3000])
    if m:
        lang = m.group(1).lower()

    # images: download each one so the EPUB carries it; drop the ones that will not come
    imgdir, n_ok, n_bad = out.parent / (out.stem + '_img'), 0, 0

    def fetch_img(m):
        nonlocal n_ok, n_bad
        alt, src = m.group(1), m.group(2).split()[0]
        if src.startswith('data:'):
            n_bad += 1
            return ''
        full = urllib.parse.urljoin(base, src)
        try:
            data, _, _ = get(full, timeout=20)
            ext = (re.search(r'\.(png|jpe?g|gif|webp|svg)', full.lower()) or [None, 'jpg'])[1]
            if ext in ('webp', 'svg'):                 # e-readers handle these badly: convert to png
                from io import BytesIO
                from PIL import Image
                if ext == 'svg':
                    raise ValueError('svg')
                im = Image.open(BytesIO(data)).convert('RGB')
                buf = BytesIO()
                im.save(buf, 'PNG')
                data, ext = buf.getvalue(), 'png'
            if len(data) < 2500:                       # tracking pixels, icons
                n_bad += 1
                return ''
            imgdir.mkdir(exist_ok=True)
            name = hashlib.md5(full.encode()).hexdigest()[:10] + '.' + ext.replace('jpeg', 'jpg')
            (imgdir / name).write_bytes(data)
            n_ok += 1
            return f'\n\n![{alt}]({imgdir.name}/{name})\n\n'
        except Exception:
            n_bad += 1
            return ''

    md = re.sub(r'!\[([^\]]*)\]\(([^)]+)\)', fetch_img, md)
    md = re.sub(r'\n{3,}', '\n\n', md).strip()
    md = re.sub(r'(?m)^# ', '## ', md)                 # the article title is the only top-level heading
    md = re.sub(r'\s*\\?\[edit\\?\]', '', md)              # Wikipedia section links
    norm = lambda t: re.sub(r'\W+', '', t.lower())
    first = re.match(r'#{2,6}\s+(.+)\n+', md)
    if first and title and norm(first.group(1)) == norm(title):
        md = md[first.end():]                          # the page repeats its own title as a heading

    head = [f'# {title or "Untitled"}', '']
    byline = ' · '.join(x for x in (author, site, date) if x)
    if byline:
        head += [f'_{byline}_', '']
    if base:
        head += [f'Source: <{base}>', '']
    out.write_text('\n'.join(head) + '\n' + md + '\n', encoding='utf-8')

    words = len(md.split())
    print(json.dumps(dict(title=title, author=author, site=site, date=date, lang=lang)))
    print(f'ARTICLE  {words:,} words, {n_ok} images kept' + (f', {n_bad} skipped' if n_bad else '') + f' -> {out}')
    print('START  ' + md[:300].replace('\n', ' '))
    print('END    ' + md[-300:].replace('\n', ' '))


if __name__ == '__main__':
    main()
