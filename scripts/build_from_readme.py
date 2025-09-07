#!/usr/bin/env python3
"""
Build master.pdf from README.md with a styled first-page Index:

- Index page (page 1) styled like your HTML:
  * 30px margins, Arial/Helvetica-like sizing: 24 (H1), 18 (H2), 12.5 (body)
  * line-height ~1.3, underlined H2s
  * H1 wraps INSIDE the content box and is centered line-by-line
  * extra spacing under the title and before the "Index" header
- Index entries wrap, page number is on the LAST line, full entry row is clickable
- Index can span multiple pages (entries never split across pages)
- Body: merged PDFs in README order; body pages numbered 1..N (index pages unnumbered)
- Bookmarks for Index + each section
- README auto-updated with a page map

Deps: PyPDF2>=3.0.0, reportlab, requests
"""

import io
import re
import sys
import datetime
from pathlib import Path

import requests
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.pagesizes import A4
from reportlab.lib.colors import black, HexColor

# ---- PyPDF2 (>=3.x) ---------------------------------------------------------
try:
    from PyPDF2 import PdfReader, PdfWriter
    try:
        from PyPDF2.generic import (
            AnnotationBuilder, DictionaryObject, NameObject, ArrayObject,
            FloatObject, NumberObject
        )
    except Exception:
        AnnotationBuilder = None
        from PyPDF2.generic import (
            DictionaryObject, NameObject, ArrayObject, FloatObject, NumberObject
        )
except Exception:
    print("PyPDF2 (>=3) is required. Install with: pip install 'PyPDF2>=3.0.0'", file=sys.stderr)
    raise

# ---- Paths / constants -------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
PDF_DIR = ROOT / "pdfs"
CACHE = ROOT / ".cache"
MASTER = PDF_DIR / "master.pdf"
FONTS_DIR = ROOT / "fonts"  # optional: drop TTFs here

BEGIN_MARK = "<!-- BEGIN MASTER INDEX -->"
END_MARK   = "<!-- END MASTER INDEX -->"

PDF_DIR.mkdir(parents=True, exist_ok=True)
CACHE.mkdir(parents=True, exist_ok=True)

# CSS-like metrics (convert px→pt at ~0.75)
PX = 0.75
MARGIN_PT = 30 * PX        # ≈22.5pt margins
TOP_FIRST_PT = 60 * PX     # ≈45pt top margin on first index page
TOP_NEXT_PT  = 30 * PX     # ≈22.5pt on subsequent index pages
BODY_FS = 12.5
H1_FS = 24
H2_FS = 18
LEADING = BODY_FS * 1.3
H1_LEADING = H1_FS * 1.2
TITLE_MB_PT = 40 * PX      # ≈30pt space under title
INDEX_TOP_EXTRA_PT = 16 * PX
LINK_COLOR = HexColor("#0077cc")
TEXT_COLOR = HexColor("#222222")

# Fonts: auto-register DejaVu/Arial if provided; fallback to Helvetica
FONT_REGULAR = "Helvetica"
FONT_BOLD = "Helvetica-Bold"

def _register_fonts():
    global FONT_REGULAR, FONT_BOLD
    candidates = [
        ("DejaVuSans", "DejaVuSans.ttf", "DejaVuSans-Bold", "DejaVuSans-Bold.ttf"),
        ("Arial", "Arial.ttf", "Arial-Bold", "Arial-Bold.ttf"),
        ("LiberationSans", "LiberationSans-Regular.ttf", "LiberationSans-Bold", "LiberationSans-Bold.ttf"),
    ]
    for fam, regular, boldfam, bold in candidates:
        rpath = FONTS_DIR / regular
        bpath = FONTS_DIR / bold
        if rpath.exists() and bpath.exists():
            try:
                pdfmetrics.registerFont(TTFont(fam, str(rpath)))
                pdfmetrics.registerFont(TTFont(boldfam, str(bpath)))
                FONT_REGULAR, FONT_BOLD = fam, boldfam
                return
            except Exception:
                continue
_register_fonts()

# ----------------------------- helpers ---------------------------------------
def human_size(n: int) -> str:
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} TB"

def strip_md_inline(s: str) -> str:
    s = re.sub(r"\*\*(.*?)\*\*", r"\1", s)           # **bold**
    s = re.sub(r"\*(.*?)\*", r"\1", s)               # *italic*
    s = re.sub(r"`(.*?)`", r"\1", s)                 # `code`
    s = re.sub(r"\[(.*?)\]\((.*?)\)", r"\1", s)      # [text](link) -> text
    return s

def read_file(p: Path) -> str:
    return p.read_text(encoding="utf-8")

def write_file(p: Path, s: str) -> None:
    p.write_text(s, encoding="utf-8")

def wrap_by_width(c: canvas.Canvas, text: str, font: str, size: float, max_width: float):
    """Word-wrap by measured widths; returns list[str] lines (at least [''] for empty)."""
    words = text.split()
    if not words:
        return [""]
    lines, line = [], ""
    for w in words:
        test = (line + " " + w).strip()
        if c.stringWidth(test, font, size) <= max_width:
            line = test
        else:
            if line:
                lines.append(line)
            if c.stringWidth(w, font, size) > max_width:
                # hard-break very long token
                buf = ""
                for ch in w:
                    if c.stringWidth(buf + ch, font, size) <= max_width:
                        buf += ch
                    else:
                        lines.append(buf)
                        buf = ch
                line = buf
            else:
                line = w
    if line:
        lines.append(line)
    return lines

# ----------------------------- README parsing --------------------------------
def parse_readme(md: str):
    """
    Extracts:
      cover: {
        'title': H1,
        'subheads': [h3 text inside the intro block],
        'intro': [paragraph lines in intro block],
        'survival': {'text','url'} | None
      }
      items: [{'title','url'}] (in the same order as README)
    The "intro block" is everything from the start until the first '---' line.
    """
    lines = md.splitlines()

    # Find first H1 as title
    title = None
    for ln in lines:
        m = re.match(r"^\s*#\s+(.*)", ln)
        if m:
            title = strip_md_inline(m.group(1).strip())
            break

    # Intro block: from start until first horizontal rule '---'
    intro_block = []
    hr_found = False
    for ln in lines:
        if ln.strip().startswith('---'):
            hr_found = True
            break
        intro_block.append(ln)

    # Collect h3 subheads and paragraph-ish lines inside intro block
    subheads = []
    intro_paras = []
    for ln in intro_block:
        h3 = re.match(r"^\s*###\s+(.*)$", ln)
        if h3:
            subheads.append(strip_md_inline(h3.group(1).strip()))
            continue
        # ignore H1 itself
        if re.match(r"^\s*#\s+", ln):
            continue
        # ignore empty lines and bullet-only top lists (not used in your README now)
        if ln.strip().startswith(("-", "*")):
            continue
        if ln.strip() == "":
            intro_paras.append("")  # preserve paragraph breaks
        else:
            # keep original lines; we'll wrap later
            intro_paras.append(strip_md_inline(ln))

    # Collapse multiple empties but keep paragraph separation
    # (simple normalize: split on blanks to paras)
    paras = []
    buf = []
    for ln in intro_paras:
        if ln == "":
            if buf:
                paras.append(" ".join(buf).strip())
                buf = []
        else:
            buf.append(ln)
    if buf:
        paras.append(" ".join(buf).strip())

    # Survival guide: first PDF link anywhere in the intro block
    survival = None
    for ln in intro_block:
        m = re.search(r"\[([^\]]+)\]\((https?://[^\s)]+\.pdf)\)", ln, re.I)
        if m:
            survival = {"text": strip_md_inline(m.group(1)), "url": m.group(2)}
            break

    # Section items AFTER the first '---'
    items = []
    if hr_found:
        after = lines[lines.index('---')+1 if '---' in lines else len(lines):]
    else:
        after = lines

    current_h3 = None
    for ln in after:
        # new section header
        h = re.match(r"^\s*###\s+(.*)\s*$", ln)
        if h:
            current_h3 = strip_md_inline(h.group(1).strip())
            continue
        # download link
        m = re.search(r"\[.*?Download PDF.*?\]\((https?://[^)]+?\.pdf)\)", ln, re.I)
        if m and current_h3:
            items.append({"title": current_h3, "url": m.group(1).strip()})

    cover = {
        "title": title or "Programme",
        "subheads": subheads,   # e.g., ["Welcome to the Doctoral Programme in ..."]
        "intro": paras,         # the paragraph text (wrapped later)
        "survival": survival,
    }
    return cover, items

# ----------------------------- download & simple PDF helpers -----------------
def download_pdf(url: str, dest: Path) -> None:
    if dest.exists() and dest.stat().st_size > 0:
        return
    with requests.get(url, stream=True, allow_redirects=True) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(65536):
                if chunk:
                    f.write(chunk)

def page_number_overlay(width: float, height: float, text: str):
    """Footer number for body pages (aligned with 30px margin)."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(width, height))
    c.setFont(FONT_REGULAR, 9)
    c.setFillColor(black)
    c.drawRightString(width - MARGIN_PT, MARGIN_PT - 4, text)
    c.save()
    buf.seek(0)
    return PdfReader(buf).pages[0]

def merge_page_safe(page_obj, overlay_page_obj):
    try:
        page_obj.merge_page(overlay_page_obj)
    except Exception:
        try:
            page_obj.mergePage(overlay_page_obj)
        except Exception:
            pass

# ----------------------------- bookmarks / links -----------------------------
def add_bookmark(writer: PdfWriter, title: str, page_index: int, parent=None):
    if hasattr(writer, "add_outline_item"):
        return writer.add_outline_item(title, page_index, parent=parent)
    if hasattr(writer, "addBookmark"):
        return writer.addBookmark(title, page_index, parent)
    return None

def add_internal_link(writer: PdfWriter, from_page: int, to_page: int, rect):
    """Clickable link from `from_page` to `to_page` over rectangle `rect`."""
    if AnnotationBuilder is not None and hasattr(writer, "add_annotation"):
        try:
            annot = AnnotationBuilder.link(rect=rect, target_page_index=to_page)
            writer.add_annotation(page_number=from_page, annotation=annot)
            return
        except Exception:
            pass
    # Low-level fallback
    try:
        page = writer.pages[from_page]
        dest_page = writer.pages[to_page]
        page_ref = getattr(dest_page, "indirect_reference", None)
        if page_ref is None:
            return
        dest = ArrayObject([page_ref, NameObject("/Fit")])
        annot = DictionaryObject()
        annot.update({
            NameObject("/Type"): NameObject("/Annot"),
            NameObject("/Subtype"): NameObject("/Link"),
            NameObject("/Rect"): ArrayObject([
                FloatObject(rect[0]), FloatObject(rect[1]),
                FloatObject(rect[2]), FloatObject(rect[3])
            ]),
            NameObject("/Border"): ArrayObject([NumberObject(0), NumberObject(0), NumberObject(0)]),
            NameObject("/Dest"): dest,
        })
        if "/Annots" in page:
            page["/Annots"].append(annot)
        else:
            page[NameObject("/Annots")] = ArrayObject([annot])
    except Exception:
        pass

# ----------------------------- styled Index (multi-page, safe) ---------------
def draw_h2_with_rule(c, W, y, text):
    c.setFont(FONT_BOLD, H2_FS)
    c.drawString(MARGIN_PT, y, text)
    y -= 6
    c.setLineWidth(1)
    c.setStrokeColor(HexColor("#cccccc"))
    c.line(MARGIN_PT, y, W - MARGIN_PT, y)
    return y - 10

def make_index_pages(cover: dict, body_entries, pagesize=A4):
    """
    Create the Index PDF.
    Returns (PdfReader, link_rects) where link_rects is [(from_page_idx, rect, body_start)]
    """
    packet = io.BytesIO()
    c = canvas.Canvas(packet, pagesize=pagesize)
    W, H = pagesize
    content_w = W - 2 * MARGIN_PT
    max_w = content_w

    current_page = 0

    def new_page(first=False):
        return H - (TOP_FIRST_PT if first else TOP_NEXT_PT)

    def ensure_space(y, needed):
        nonlocal current_page
        if y - needed < MARGIN_PT:
            c.showPage()
            current_page += 1
            return new_page(first=False)
        return y

    link_rects = []
    y = new_page(first=True)

    # ----- H1 TITLE (wrapped & centered within margins) -----
    c.setFillColor(TEXT_COLOR)
    c.setFont(FONT_BOLD, H1_FS)
    title = cover.get("title", "Programme")
    title_lines = wrap_by_width(c, title, FONT_BOLD, H1_FS, max_w)
    y = ensure_space(y, H1_LEADING * len(title_lines) + TITLE_MB_PT)
    for line in title_lines:
        lw = c.stringWidth(line, FONT_BOLD, H1_FS)
        x = MARGIN_PT + (content_w - lw) / 2.0
        c.drawString(x, y, line)
        y -= H1_LEADING
    y -= TITLE_MB_PT  # breathing room under title

    # ----- OPTIONAL H3 SUBHEADS (rendered as H2 style) -----
    subheads = cover.get("subheads") or []
    for sh in subheads:
        y = ensure_space(y, H2_FS + 16)
        y = draw_h2_with_rule(c, W, y, sh)

    # ----- INTRO PARAGRAPHS -----
    c.setFont(FONT_REGULAR, BODY_FS)
    for para in (cover.get("intro") or []):
        if not para:
            continue
        lines = wrap_by_width(c, para, FONT_REGULAR, BODY_FS, max_w)
        for line in lines:
            y = ensure_space(y, LEADING)
            c.drawString(MARGIN_PT, y, line)
            y -= LEADING
        y -= 4

    # ----- SURVIVAL GUIDE LINK -----
    surv = cover.get("survival")
    if surv:
        label = f"Helpful: {surv['text']}"
        lw = c.stringWidth(label, FONT_REGULAR, BODY_FS)
        y = ensure_space(y, LEADING)
        c.setFont(FONT_REGULAR, BODY_FS)
        c.setFillColor(LINK_COLOR)
        c.drawString(MARGIN_PT, y, label)
        c.linkURL(surv["url"], (MARGIN_PT, y - 2, MARGIN_PT + lw, y + 12), relative=0)
        c.setFillColor(TEXT_COLOR)
        y -= LEADING

    # ----- EXTRA GAP BEFORE INDEX HEADER -----
    y = ensure_space(y, INDEX_TOP_EXTRA_PT)
    y -= INDEX_TOP_EXTRA_PT

    # ----- INDEX HEADER -----
    y = ensure_space(y, H2_FS + 16)
    y = draw_h2_with_rule(c, W, y, "Index")
    c.setFont(FONT_REGULAR, BODY_FS)

    # ----- INDEX ENTRIES (never split an entry across pages) -----
    for title, body_start in body_entries:
        text_w = max_w - 48  # reserve right edge for page number
        lines = wrap_by_width(c, title, FONT_REGULAR, BODY_FS, text_w)
        required = LEADING * len(lines)
        # move to next page if this entry doesn't fit here
        if y - required < MARGIN_PT:
            c.showPage()
            current_page += 1
            y = new_page(first=False)
            c.setFont(FONT_REGULAR, BODY_FS)
        rect_top = y
        for i, line in enumerate(lines):
            c.drawString(MARGIN_PT, y, line)
            if i == len(lines) - 1:
                c.drawRightString(W - MARGIN_PT, y, f"{body_start}")
            y -= LEADING
        rect_bottom = y + LEADING
        rect = (MARGIN_PT, rect_bottom - 2, W - MARGIN_PT, rect_top + 12)
        link_rects.append((current_page, rect, body_start))

    # finalize (do NOT add an extra blank page)
    c.save()
    packet.seek(0)
    index_reader = PdfReader(packet)
    return index_reader, link_rects

# ----------------------------- assembly --------------------------------------
def build_master(cover: dict, items: list[dict]):
    # 1) Fetch sources + counts
    cache, counts = [], []
    for idx, it in enumerate(items):
        dest = CACHE / f"src_{idx:03d}.pdf"
        try:
            download_pdf(it["url"], dest)
            count = len(PdfReader(dest).pages)
        except Exception as e:
            print(f"[warn] Skipping '{it['title']}' due to download/read error: {e}", file=sys.stderr)
            continue
        cache.append((it, dest))
        counts.append(count)

    # 2) Compute body numbering (start_body/end_body)
    body_map = []
    cursor = 1
    for (it, _), count in zip(cache, counts):
        body_map.append({"title": it["title"], "start_body": cursor, "end_body": cursor + count - 1})
        cursor += count

    # 3) Build Index PDF (may be multi-page)
    index_pdf, link_rects = make_index_pages(
        cover,
        [(ent["title"], ent["start_body"]) for ent in body_map],
        pagesize=A4,
    )
    index_pages = len(index_pdf.pages)

    # 4) Compute absolute pages now that we know index length
    page_map = []
    for ent, count in zip(body_map, counts):
        start_abs = index_pages + ent["start_body"]
        end_abs = index_pages + ent["end_body"]
        page_map.append({
            "title": ent["title"],
            "start_body": ent["start_body"], "end_body": ent["end_body"],
            "start_abs": start_abs, "end_abs": end_abs,
        })

    # 5) Assemble
    writer = PdfWriter()

    # Add index pages
    for p in index_pdf.pages:
        writer.add_page(p)
    add_bookmark(writer, "Index", 0)

    # Body + bookmarks + footer numbers
    sections_parent = add_bookmark(writer, "Sections", index_pages)
    abs_page_index = index_pages  # zero-based index where body starts

    for (it, src_path), meta in zip(cache, page_map):
        start_idx = abs_page_index
        add_bookmark(writer, it["title"], start_idx, parent=sections_parent)

        src = PdfReader(src_path)
        num = meta["start_body"]
        for pg in src.pages:
            w, h = float(pg.mediabox.width), float(pg.mediabox.height)
            overlay = page_number_overlay(w, h, f"{num}")
            merge_page_safe(pg, overlay)
            writer.add_page(pg)
            abs_page_index += 1
            num += 1

    # 6) Wire clickable index links (map body_start -> absolute)
    for from_page, rect, body_start in link_rects:
        target_idx = index_pages + (body_start - 1)  # zero-based
        add_internal_link(writer, from_page, target_idx, rect)

    # 7) Write
    with open(MASTER, "wb") as f:
        writer.write(f)

    return page_map

# ----------------------------- README update ---------------------------------
def update_readme(md: str, page_map: list[dict]) -> str:
    size = human_size(MASTER.stat().st_size) if MASTER.exists() else "0 B"
    updated = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        BEGIN_MARK, "",
        "## Master PDF",
        f"- 📘 **[Download Master PDF](pdfs/{MASTER.name})** ({size})",
        "- _Index page(s) are unnumbered; body pages start at 1._",
        f"- _Last updated: {updated}_", "",
        "### Page map (body numbering)",
    ]
    if not page_map:
        lines.append("- *(no PDFs found in README)*")
    else:
        for ent in page_map:
            lines.append(
                f"- **{ent['title']}** — pp. {ent['start_body']}–{ent['end_body']} "
                f"(open: [p.{ent['start_body']}](pdfs/{MASTER.name}#page={ent['start_abs']}))"
            )
    lines += ["", END_MARK]
    block = "\n".join(lines)

    if BEGIN_MARK in md and END_MARK in md:
        import re as _re
        new_md = _re.sub(
            rf"{_re.escape(BEGIN_MARK)}.*?{_re.escape(END_MARK)}",
            block, md, flags=_re.DOTALL,
        )
    else:
        sep = "\n\n---\n\n" if not md.endswith("\n") else "\n---\n\n"
        new_md = md + sep + block + "\n"
    return new_md

# ----------------------------- main ------------------------------------------
def main():
    if not README.exists():
        print("README.md missing", file=sys.stderr)
        sys.exit(1)

    md = read_file(README)
    cover, items = parse_readme(md)
    if not items:
        print("No 'Download PDF' links found in README. Nothing to do.", file=sys.stderr)
        return

    page_map = build_master(cover, items)
    new_md = update_readme(md, page_map)
    if new_md != md:
        write_file(README, new_md)

if __name__ == "__main__":
    main()
