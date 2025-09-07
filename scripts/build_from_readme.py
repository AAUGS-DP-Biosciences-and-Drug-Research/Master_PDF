#!/usr/bin/env python3
"""
Build master.pdf from README.md with a styled first-page Index:
- Index page (page 1) styled like your HTML (30px margins, 24/18/12.5 fonts, line-height 1.3, underlined h2).
- Proper word-wrapping by measured widths; never overflows bottom margin; continues onto extra Index pages if needed.
- Body: merged PDFs in README order, with bookmarks; body pages numbered 1..N in footers (index pages have no footer numbers).
- README auto-updated with a page map (links use absolute page numbers that account for multi-page index).

Dependencies: PyPDF2>=3.0.0, reportlab, requests
"""

import io
import re
import sys
import datetime
from pathlib import Path

import requests
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import inch
from reportlab.lib.colors import black, HexColor

# ---- PyPDF2 (>=3.x) ---------------------------------------------------------
try:
    from PyPDF2 import PdfReader, PdfWriter
    # AnnotationBuilder lives under generic in PyPDF2 3.x
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

BEGIN_MARK = "<!-- BEGIN MASTER INDEX -->"
END_MARK   = "<!-- END MASTER INDEX -->"

PDF_DIR.mkdir(parents=True, exist_ok=True)
CACHE.mkdir(parents=True, exist_ok=True)

# CSS-like metrics (converted from px->pt where 1px≈0.75pt)
PX = 0.75
MARGIN_PT = 30 * PX        # ~22.5pt
TOP_FIRST_PT = 60 * PX     # ~45pt (title page top margin)
TOP_NEXT_PT  = 30 * PX     # ~22.5pt (subsequent index pages)
BODY_FS = 12.5
H1_FS = 24
H2_FS = 18
LEADING = BODY_FS * 1.3
LINK_COLOR = HexColor("#0077cc")
TEXT_COLOR = HexColor("#222222")

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
    """Word-wrap by measured widths; returns list[str] lines."""
    words = text.split()
    lines, line = [], ""
    for w in words:
        test = (line + " " + w).strip()
        if c.stringWidth(test, font, size) <= max_width:
            line = test
        else:
            if line:
                lines.append(line)
            # very long single token: hard-break by chars
            if c.stringWidth(w, font, size) > max_width:
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
    """Extract cover/top info and section list (title + PDF url)."""
    lines = md.splitlines()

    # Title
    title = None
    for ln in lines:
        m = re.match(r"^\s*#\s+(.*)", ln)
        if m:
            title = strip_md_inline(m.group(1).strip())
            break

    # Top block until first ### or master markers
    top = []
    for ln in lines:
        if ln.strip() == BEGIN_MARK:
            break
        if re.match(r"^\s*###\s+", ln):
            break
        top.append(ln)

    # Intro after H1 until '---'
    intro_lines, seen_h1 = [], False
    for ln in top:
        if not seen_h1:
            if re.match(r"^\s*#\s+", ln):
                seen_h1 = True
            continue
        if ln.strip().startswith("---"):
            break
        intro_lines.append(ln)
    intro_text = strip_md_inline("\n".join(intro_lines).strip())
    intro = [ln for ln in intro_text.splitlines() if ln.strip()]

    # Majors between '## Major Subjects' and next '---'
    majors, capture = [], False
    for ln in top:
        if re.match(r"^\s*##\s+Major Subjects", ln):
            capture = True
            continue
        if capture:
            if ln.strip().startswith("---"):
                break
            m = re.match(r"^\s*-\s+(.*)", ln)
            if m:
                majors.append(strip_md_inline(m.group(1).strip()))

    # Survival guide: first PDF link in top block
    survival = None
    for ln in top:
        m = re.search(r"\[([^\]]+)\]\((https?://[^\s)]+\.pdf)\)", ln, re.I)
        if m:
            survival = {"text": strip_md_inline(m.group(1)), "url": m.group(2)}
            break

    # Sections: ### heading + Download PDF link
    items, current_h3 = [], None
    for ln in lines:
        if ln.strip() == BEGIN_MARK:
            break
        h = re.match(r"^\s*###\s+(.*)\s*$", ln)
        if h:
            current_h3 = strip_md_inline(h.group(1).strip())
            continue
        m = re.search(r"\[.*?Download PDF.*?\]\((https?://[^)]+?\.pdf)\)", ln, re.I)
        if m and current_h3:
            items.append({"title": current_h3, "url": m.group(1).strip()})

    cover = {
        "title": title or "Programme",
        "intro": intro,
        "majors": majors,
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
    """Footer page number for body pages (right, within margin)."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(width, height))
    c.setFont("Helvetica", 9)
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

# ----------------------------- styled Index (multi-page) ---------------------
def make_styled_index_pdf(cover: dict, toc_entries, pagesize=A4):
    """
    Create the Index PDF. Returns (PdfReader, link_rects) where link_rects is a
    list of (from_page_idx, rect, body_start_page).
    """
    packet = io.BytesIO()
    c = canvas.Canvas(packet, pagesize=pagesize)
    W, H = pagesize

    def new_page(first=False):
        c.setFillColor(TEXT_COLOR)
        return (H - (TOP_FIRST_PT if first else TOP_NEXT_PT))

    def ensure_space(y, needed, page_index):
        if y - needed < MARGIN_PT:
            c.showPage()
            return (new_page(first=False), page_index + 1)
        return (y, page_index)

    y = new_page(first=True)
    page_idx = 0
    link_rects = []

    # Title centered (page 1 only)
    c.setFont("Helvetica-Bold", H1_FS)
    title = cover.get("title", "Programme")
    tw = c.stringWidth(title, "Helvetica-Bold", H1_FS)
    c.drawString((W - tw) / 2.0, y, title)
    y -= 20

    # Intro paragraphs
    c.setFont("Helvetica", BODY_FS)
    max_w = W - 2 * MARGIN_PT
    for para in (cover.get("intro") or []):
        lines = wrap_by_width(c, para, "Helvetica", BODY_FS, max_w)
        for line in lines:
            y, page_idx = ensure_space(y, LEADING, page_idx)
            c.drawString(MARGIN_PT, y, line)
            y -= LEADING
        y -= 4  # small spacing between paragraphs

    # Major Subjects block
    majors = cover.get("majors") or []
    if majors:
        # h2
        y, page_idx = ensure_space(y, H2_FS + 16, page_idx)
        c.setFont("Helvetica-Bold", H2_FS)
        c.drawString(MARGIN_PT, y, "Major Subjects in the Programme")
        y -= 6
        c.setLineWidth(1)
        c.setStrokeColor(HexColor("#cccccc"))
        c.line(MARGIN_PT, y, W - MARGIN_PT, y)
        y -= 10
        # list
        c.setFont("Helvetica", BODY_FS)
        for m in majors:
            # bullet + wrapped text with hanging indent
            prefix = "• "
            indent = c.stringWidth(prefix, "Helvetica", BODY_FS)
            lines = wrap_by_width(c, m, "Helvetica", BODY_FS, max_w - indent)
            for i, line in enumerate(lines):
                y, page_idx = ensure_space(y, LEADING, page_idx)
                if i == 0:
                    c.drawString(MARGIN_PT, y, prefix + line)
                else:
                    c.drawString(MARGIN_PT + indent, y, line)
                y -= LEADING

    # Survival guide link
    surv = cover.get("survival")
    if surv:
        label = f"Helpful: {surv['text']}"
        label_w = c.stringWidth(label, "Helvetica", BODY_FS)
        y, page_idx = ensure_space(y, LEADING, page_idx)
        c.setFont("Helvetica", BODY_FS)
        c.setFillColor(LINK_COLOR)
        c.drawString(MARGIN_PT, y, label)
        c.linkURL(surv["url"], (MARGIN_PT, y - 2, MARGIN_PT + label_w, y + 12), relative=0)
        c.setFillColor(TEXT_COLOR)
        y -= LEADING

    # Index header (h2)
    y, page_idx = ensure_space(y, H2_FS + 16, page_idx)
    c.setFont("Helvetica-Bold", H2_FS)
    c.drawString(MARGIN_PT, y, "Index")
    y -= 6
    c.setLineWidth(1)
    c.setStrokeColor(HexColor("#cccccc"))
    c.line(MARGIN_PT, y, W - MARGIN_PT, y)
    y -= 10
    c.setFont("Helvetica", BODY_FS)

    # Entries
    for title, body_start in toc_entries:
        # Wrap title if too long (single visual line preferred)
        line = title
        # draw row
        y, page_idx = ensure_space(y, LEADING, page_idx)
        # Title left
        c.setFont("Helvetica", BODY_FS)
        # reserve room for page number at right
        c.drawString(MARGIN_PT, y, line)
        # Page number right
        c.drawRightString(W - MARGIN_PT, y, f"{body_start}")
        # clickable across the whole row
        rect = (MARGIN_PT, y - 2, W - MARGIN_PT, y + 12)
        link_rects.append((page_idx, rect, body_start))
        y -= LEADING

    # Footer (each index page)
    # We’ll add a small footer text on each index page after building all pages:
    # (Cannot iterate pages directly here because we’re streaming)

    # Finish
    c.showPage()
    c.save()
    packet.seek(0)
    index_reader = PdfReader(packet)

    # Add "Generated from README.md" footer to each index page via a light overlay
    # (keep style consistent; not strictly necessary if you prefer to omit)
    # Skipped for simplicity since content is already bounded; can be added similarly to page numbers.

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

    # 2) Compute body numbering (start_body/end_body); absolute pages depend on index length
    body_map = []
    cursor = 1
    for (it, _), count in zip(cache, counts):
        body_map.append({"title": it["title"], "start_body": cursor, "end_body": cursor + count - 1})
        cursor += count

    # 3) Build Index PDF (may be multi-page). It only needs (title, start_body).
    toc_entries = [(ent["title"], ent["start_body"]) for ent in body_map]
    index_pdf, link_rects = make_styled_index_pdf(cover, toc_entries, pagesize=A4)
    index_pages = len(index_pdf.pages)

    # 4) Now compute absolute pages
    page_map = []
    for ent, count in zip(body_map, counts):
        start_abs = index_pages + ent["start_body"]
        end_abs = index_pages + ent["end_body"]
        page_map.append({
            "title": ent["title"],
            "start_body": ent["start_body"], "end_body": ent["end_body"],
            "start_abs": start_abs, "end_abs": end_abs,
        })

    # 5) Assemble final PDF
    writer = PdfWriter()

    # Add all index pages
    for p in index_pdf.pages:
        writer.add_page(p)
    add_bookmark(writer, "Index", 0)

    # Body with bookmarks + footer numbers
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

    # 6) Wire up clickable index links
    for from_page, rect, body_start in link_rects:
        target_idx = index_pages + (body_start - 1)  # zero-based writer index
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
