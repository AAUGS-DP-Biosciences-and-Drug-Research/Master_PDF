#!/usr/bin/env python3
"""
Build master.pdf from README.md with a styled first-page Index:
- Index page (page 1) styled to match your HTML (margins, fonts, underlined h2),
  including Title, Intro, Major Subjects, Survival Guide link, and a clickable index
  of sections with page numbers.
- Body: merged PDFs in README order, with bookmarks and footer numbers starting at 1.
- README gets an auto-updated "Master PDF" section with a page map.

Dependencies: PyPDF2>=3.0.0, reportlab, requests
"""

import io
import re
import sys
import datetime
import textwrap
from pathlib import Path

import requests
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import inch
from reportlab.lib.colors import black, HexColor

# ---- PyPDF2 (>=3.x) ---------------------------------------------------------
try:
    from PyPDF2 import PdfReader, PdfWriter
    # AnnotationBuilder is under generic in PyPDF2 3.x
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

# ---- Paths / Constants -------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
PDF_DIR = ROOT / "pdfs"
CACHE = ROOT / ".cache"
MASTER = PDF_DIR / "master.pdf"

BEGIN_MARK = "<!-- BEGIN MASTER INDEX -->"
END_MARK   = "<!-- END MASTER INDEX -->"

PDF_DIR.mkdir(parents=True, exist_ok=True)
CACHE.mkdir(parents=True, exist_ok=True)

# ---- Utility helpers ---------------------------------------------------------
def human_size(n: int) -> str:
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} TB"

def strip_md_inline(s: str) -> str:
    s = re.sub(r"\*\*(.*?)\*\*", r"\1", s)             # **bold**
    s = re.sub(r"\*(.*?)\*", r"\1", s)                 # *italic*
    s = re.sub(r"`(.*?)`", r"\1", s)                   # `code`
    s = re.sub(r"\[(.*?)\]\((.*?)\)", r"\1", s)        # [text](link) -> text
    return s

def read_file(p: Path) -> str:
    return p.read_text(encoding="utf-8")

def write_file(p: Path, s: str) -> None:
    p.write_text(s, encoding="utf-8")

# ---- README parsing ----------------------------------------------------------
def parse_readme(md: str):
    """
    Returns:
      cover: {
        'title': str,
        'intro': [lines],
        'majors': [items],
        'survival': {'text': str, 'url': str} | None
      }
      items: [{'title': str, 'url': str}]  # in README order
    """
    lines = md.splitlines()

    # Title (first H1)
    title = None
    for ln in lines:
        m = re.match(r"^\s*#\s+(.*)", ln)
        if m:
            title = strip_md_inline(m.group(1).strip())
            break

    # "Top block" (until first ### section or master markers)
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
        # FIXED regex (no stray ')'): match any [text](https://...pdf)
        m = re.search(r"\[([^\]]+)\]\((https?://[^\s)]+\.pdf)\)", ln, re.I)
        if m:
            survival = {"text": strip_md_inline(m.group(1)), "url": m.group(2)}
            break

    # Section items: ### title + 'Download PDF' link
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

# ---- Download & simple PDF helpers ------------------------------------------
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
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(width, height))
    c.setFont("Helvetica", 9)
    c.setFillColor(black)
    c.drawRightString(width - 36, 18, text)
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

# ---- Bookmarks / Links -------------------------------------------------------
def add_bookmark(writer: PdfWriter, title: str, page_index: int, parent=None):
    if hasattr(writer, "add_outline_item"):
        return writer.add_outline_item(title, page_index, parent=parent)
    if hasattr(writer, "addBookmark"):
        return writer.addBookmark(title, page_index, parent)
    return None

def add_internal_link(writer: PdfWriter, from_page: int, to_page: int, rect):
    """
    Add a clickable link on page `from_page` that jumps to `to_page`.
    Prefers AnnotationBuilder; falls back to low-level annotation.
    """
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

# ---- Styled Index page (matches your HTML look) ------------------------------
def make_styled_index_pdf(cover: dict, toc_entries, pagesize=A4):
    """
    Build a single "Index" page combining:
      - Title (center)
      - Intro paragraph(s)
      - 'Major Subjects' (h2 with underline) + bullet list
      - Survival guide link (blue)
      - 'Index' (h2 with underline) listing section titles with body page numbers
    Returns (PdfReader, link_rects) where link_rects = [(rect, target_page_index)]
    """
    # CSS -> points: px ~ 0.75pt
    px = 0.75
    margin = 30 * px               # ≈ 22.5pt left/right margins
    title_mt = 60 * px
    body_fs = 12.5
    lh = body_fs * 1.3
    h1_fs = 24
    h2_fs = 18
    link_color = HexColor("#0077cc")
    text_color = HexColor("#222222")

    packet = io.BytesIO()
    c = canvas.Canvas(packet, pagesize=pagesize)
    W, H = pagesize

    c.setFillColor(text_color)

    y = H - title_mt

    # TITLE centered
    c.setFont("Helvetica-Bold", h1_fs)
    title = cover.get("title", "Programme")
    tw = c.stringWidth(title, "Helvetica-Bold", h1_fs)
    c.drawString((W - tw) / 2.0, y, title)
    y -= 20

    # Intro
    c.setFont("Helvetica", body_fs)
    for para in cover.get("intro") or []:
        for line in textwrap.wrap(para, width=90):
            c.drawString(margin, y, line)
            y -= lh
        y -= 4

    # Major Subjects
    majors = cover.get("majors") or []
    if majors:
        y -= 8
        c.setFont("Helvetica-Bold", h2_fs)
        c.drawString(margin, y, "Major Subjects in the Programme")
        y -= 6
        c.setLineWidth(1)
        c.setStrokeColor(HexColor("#cccccc"))
        c.line(margin, y, W - margin, y)
        y -= 10
        c.setFont("Helvetica", body_fs)
        for m in majors:
            c.drawString(margin + 14, y, f"• {m}")
            y -= lh - 2

    # Survival guide
    surv = cover.get("survival")
    if surv:
        y -= 6
        label = f"Helpful: {surv['text']}"
        c.setFont("Helvetica", body_fs)
        c.setFillColor(link_color)
        c.drawString(margin, y, label)
        tw = c.stringWidth(label, "Helvetica", body_fs)
        c.linkURL(surv["url"], (margin, y-2, margin+tw, y+12), relative=0)
        c.setFillColor(text_color)
        y -= lh

    # INDEX header
    y -= 4
    c.setFont("Helvetica-Bold", h2_fs)
    c.drawString(margin, y, "Index")
    y -= 6
    c.setLineWidth(1)
    c.setStrokeColor(HexColor("#cccccc"))
    c.line(margin, y, W - margin, y)
    y -= 10

    # Entries
    c.setFont("Helvetica", body_fs)
    link_rects = []
    for title, body_start, abs_start in toc_entries:
        line = textwrap.shorten(title, width=90, placeholder="…")
        c.drawString(margin, y, line)
        c.drawRightString(W - margin, y, f"{body_start}")
        rect = (margin, y-2, W - margin, y+12)
        link_rects.append((rect, abs_start - 1))
        y -= lh
        if y < (margin + 36):
            break

    # Footer
    c.setFont("Helvetica", 9)
    c.setFillColor(text_color)
    c.drawRightString(W - margin, margin, "Generated from README.md")

    c.showPage()
    c.save()
    packet.seek(0)
    return PdfReader(packet), link_rects

# ---- Assembly ---------------------------------------------------------------
def build_master(cover: dict, items: list[dict]):
    # 1) Fetch sources + counts
    cache = []
    counts = []
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

    # 2) Page mapping (Index page before body)
    ABS_OFFSET = 1
    page_map = []
    body_cursor = 1
    abs_cursor = ABS_OFFSET + 1
    for (it, _), count in zip(cache, counts):
        start_body = body_cursor
        end_body = body_cursor + count - 1
        start_abs = abs_cursor
        end_abs = abs_cursor + count - 1
        page_map.append({
            "title": it["title"],
            "start_body": start_body, "end_body": end_body,
            "start_abs": start_abs,   "end_abs": end_abs,
        })
        body_cursor += count
        abs_cursor += count

    # 3) Build Index page
    toc_entries = [(ent["title"], ent["start_body"], ent["start_abs"]) for ent in page_map]
    index_pdf, index_link_rects = make_styled_index_pdf(cover, toc_entries, pagesize=A4)

    # 4) Assemble final PDF
    writer = PdfWriter()

    # Index page + bookmark
    writer.add_page(index_pdf.pages[0])
    add_bookmark(writer, "Index", 0)

    # Body with bookmarks + footer numbering
    sections_parent = add_bookmark(writer, "Sections", 1)
    abs_page_index = 1  # body starts at page 2 (zero-based)

    for (it, src_path), meta in zip(cache, page_map):
        start_idx = abs_page_index
        add_bookmark(writer, it["title"], start_idx, parent=sections_parent)

        src = PdfReader(src_path)
        body_page_num = meta["start_body"]
        for pg in src.pages:
            w, h = float(pg.mediabox.width), float(pg.mediabox.height)
            overlay = page_number_overlay(w, h, f"{body_page_num}")
            merge_page_safe(pg, overlay)
            writer.add_page(pg)
            abs_page_index += 1
            body_page_num += 1

    # 5) Make index rows clickable
    from_page = 0
    for rect, target_idx in index_link_rects:
        add_internal_link(writer, from_page, target_idx, rect)

    # 6) Write output
    with open(MASTER, "wb") as f:
        writer.write(f)

    return page_map

# ---- README update ----------------------------------------------------------
def update_readme(md: str, page_map: list[dict]) -> str:
    size = human_size(MASTER.stat().st_size) if MASTER.exists() else "0 B"
    updated = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        BEGIN_MARK,
        "",
        "## Master PDF",
        f"- 📘 **[Download Master PDF](pdfs/{MASTER.name})** ({size})",
        "- _Index page is unnumbered; body pages start at 1._",
        f"- _Last updated: {updated}_",
        "",
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
        new_md = re.sub(
            rf"{re.escape(BEGIN_MARK)}.*?{re.escape(END_MARK)}",
            block,
            md,
            flags=re.DOTALL,
        )
    else:
        sep = "\n\n---\n\n" if not md.endswith("\n") else "\n---\n\n"
        new_md = md + sep + block + "\n"
    return new_md

# ---- main -------------------------------------------------------------------
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
