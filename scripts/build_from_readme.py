#!/usr/bin/env python3
"""
Build a polished master.pdf from README.md:
- Cover page (from README top content)
- Clickable Contents page
- Merged body with bookmarks & page numbers (starting at 1 on the body)
- README auto-updated with a "Master PDF" section + page map

Uses PyPDF2 >= 3.x only (no pypdf required).
"""

import io
import re
import os
import sys
import datetime
import textwrap
from pathlib import Path

# ---- PyPDF2 imports (>=3.x) -------------------------------------------------
try:
    from PyPDF2 import PdfReader, PdfWriter
    # AnnotationBuilder lives under generic in PyPDF2 3.x
    try:
        from PyPDF2.generic import (
            AnnotationBuilder, DictionaryObject, NameObject, ArrayObject,
            FloatObject, NumberObject
        )
    except Exception:
        # Some builds expose AnnotationBuilder via PyPDF2.annotations
        AnnotationBuilder = None
        from PyPDF2.generic import (
            DictionaryObject, NameObject, ArrayObject, FloatObject, NumberObject
        )
except Exception as e:
    print("PyPDF2 (>=3) is required. Install with: pip install 'PyPDF2>=3.0.0'", file=sys.stderr)
    raise

import requests
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import inch

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
PDF_DIR = ROOT / "pdfs"
CACHE = ROOT / ".cache"
MASTER = PDF_DIR / "master.pdf"

BEGIN_MARK = "<!-- BEGIN MASTER INDEX -->"
END_MARK   = "<!-- END MASTER INDEX -->"

PDF_DIR.mkdir(parents=True, exist_ok=True)
CACHE.mkdir(parents=True, exist_ok=True)

# ----------------------------- utilities -----------------------------

def human_size(n: int) -> str:
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} TB"

def strip_md_inline(s: str) -> str:
    """Minimal inline markdown stripper for cover text."""
    s = re.sub(r"\*\*(.*?)\*\*", r"\1", s)           # **bold**
    s = re.sub(r"\*(.*?)\*", r"\1", s)               # *italic*
    s = re.sub(r"`(.*?)`", r"\1", s)                 # `code`
    s = re.sub(r"\[(.*?)\]\((.*?)\)", r"\1", s)      # [text](link) -> text
    return s

def read_file(p: Path) -> str:
    return p.read_text(encoding="utf-8")

def write_file(p: Path, s: str) -> None:
    p.write_text(s, encoding="utf-8")

# ----------------------------- README parsing -----------------------------

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

    # Find first H1 as title
    title = None
    for ln in lines:
        m = re.match(r"^\s*#\s+(.*)", ln)
        if m:
            title = strip_md_inline(m.group(1).strip())
            break

    # Collect "top block" lines until first ### section or the master markers
    top = []
    for ln in lines:
        if ln.strip() == BEGIN_MARK:
            break
        if re.match(r"^\s*###\s+", ln):
            break
        top.append(ln)

    # Intro: the paragraph(s) after H1 until a horizontal rule '---'
    intro_lines = []
    seen_h1 = False
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

    # Majors list between '## Major Subjects' and the next '---'
    majors = []
    capture = False
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

    # Survival guide: first PDF link in the top block
    survival = None
    for ln in top:
        m = re.search(r"\[(.+?)\]\((https?://[^\s)]+\.pdf)\)", ln, re.I)
        if m:
            survival = {"text": strip_md_inline(m.group(1)), "url": m.group(2)}
            break

    # Section items: each "### <Title>" with a following "Download PDF" link
    items = []
    current_h3 = None
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

# ----------------------------- PDF primitives -----------------------------

def download_pdf(url: str, dest: Path) -> None:
    """Download url -> dest (idempotent)."""
    if dest.exists() and dest.stat().st_size > 0:
        return
    with requests.get(url, stream=True, allow_redirects=True) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(65536):
                if chunk:
                    f.write(chunk)

def make_cover_pdf(cover: dict, pagesize=A4) -> PdfReader:
    """1-page cover constructed from README's top content."""
    packet = io.BytesIO()
    c = canvas.Canvas(packet, pagesize=pagesize)
    W, H = pagesize
    margin = 1.0 * inch
    y = H - 1.4 * inch

    # Title
    c.setFont("Helvetica-Bold", 24)
    c.drawString(margin, y, cover["title"])
    y -= 0.5 * inch

    # Intro paragraphs
    c.setFont("Helvetica", 12)
    for para in cover.get("intro") or []:
        if para.strip():
            for line in textwrap.wrap(para, width=90):
                c.drawString(margin, y, line)
                y -= 14
            y -= 6

    # Majors
    majors = cover.get("majors") or []
    if majors:
        y -= 10
        c.setFont("Helvetica-Bold", 14)
        c.drawString(margin, y, "Major Subjects in the Programme")
        y -= 18
        c.setFont("Helvetica", 12)
        for m in majors:
            c.drawString(margin + 14, y, f"• {m}")
            y -= 14

    # Survival guide (clickable)
    surv = cover.get("survival")
    if surv:
        y -= 20
        c.setFont("Helvetica", 12)
        label = f"Helpful: {surv['text']}"
        c.drawString(margin, y, label)
        tw = c.stringWidth(label, "Helvetica", 12)
        c.linkURL(surv["url"], (margin, y - 2, margin + tw, y + 12), relative=0)

    # Footer
    c.setFont("Helvetica", 9)
    c.drawRightString(W - margin, 0.6 * inch, "Generated from README.md")

    c.showPage()
    c.save()
    packet.seek(0)
    return PdfReader(packet)

def make_contents_pdf(entries, pagesize=A4):
    """
    Build a 1-page "Contents" PDF.
    entries: list of tuples (title, body_start_page_num, absolute_start_index)
    Returns: (PdfReader, link_rects) where link_rects = [(rect, target_page_index), ...]
    """
    packet = io.BytesIO()
    c = canvas.Canvas(packet, pagesize=pagesize)
    W, H = pagesize
    margin = 1.0 * inch
    y = H - 1.2 * inch

    c.setFont("Helvetica-Bold", 20)
    c.drawString(margin, y, "Contents")
    y -= 0.35 * inch

    c.setFont("Helvetica", 12)
    link_rects = []
    for title, body_start, abs_start in entries:
        line = textwrap.shorten(title, width=100, placeholder="…")
        c.drawString(margin, y, line)
        c.drawRightString(W - margin, y, f"{body_start}")
        rect = (margin, y - 2, W - margin, y + 12)
        link_rects.append((rect, abs_start - 1))  # zero-based target page index
        y -= 16
        if y < 1.0 * inch:
            break  # keep ToC to a single page for simplicity

    c.showPage()
    c.save()
    packet.seek(0)
    return PdfReader(packet), link_rects

def page_number_overlay(width: float, height: float, text: str):
    """Create a single-page PDF containing just the footer page number."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(width, height))
    c.setFont("Helvetica", 9)
    c.drawRightString(width - 36, 18, text)
    c.save()
    buf.seek(0)
    return PdfReader(buf).pages[0]

# ----------------------------- writer helpers -----------------------------

def add_bookmark(writer: PdfWriter, title: str, page_index: int, parent=None):
    """Add an outline/bookmark (PyPDF2 3.x uses add_outline_item)."""
    if hasattr(writer, "add_outline_item"):
        return writer.add_outline_item(title, page_index, parent=parent)
    if hasattr(writer, "addBookmark"):  # older PyPDF2 (<3)
        return writer.addBookmark(title, page_index, parent)
    return None

def add_internal_link(writer: PdfWriter, from_page: int, to_page: int, rect):
    """
    Create a clickable link on page `from_page` that jumps to `to_page`.
    Preferred: AnnotationBuilder + writer.add_annotation (PyPDF2 >=3).
    Fallback: low-level annotation.
    """
    if AnnotationBuilder is not None and hasattr(writer, "add_annotation"):
        try:
            annot = AnnotationBuilder.link(rect=rect, target_page_index=to_page)
            writer.add_annotation(page_number=from_page, annotation=annot)
            return
        except Exception:
            pass  # fall through to low-level

    # Low-level fallback annotation (/Link with /Dest)
    try:
        page = writer.pages[from_page]
        dest_page = writer.pages[to_page]
        # Some builds expose .indirect_reference; if not, give up quietly
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
        # If even this fails, skip ToC links; bookmarks still work.
        pass

def merge_page_safe(page_obj, overlay_page_obj):
    """Merge overlay_page_obj onto page_obj across PyPDF2 versions."""
    try:
        page_obj.merge_page(overlay_page_obj)  # modern
    except Exception:
        try:
            page_obj.mergePage(overlay_page_obj)  # legacy
        except Exception:
            pass

# ----------------------------- assembly -----------------------------

def build_master(cover: dict, items: list[dict]):
    """
    Build master.pdf with:
      - Cover (page 1)
      - Contents (page 2)
      - Body: merged PDFs in README order, numbered starting at 1 in footers
      - Bookmarks: Cover, Contents, and each section under "Sections"
    Returns: page_map for README (body numbering + absolute targets).
    """
    # 1) Download sources and read page counts
    cache_paths = []
    page_counts = []
    for idx, it in enumerate(items):
        dest = CACHE / f"src_{idx:03d}.pdf"
        try:
            download_pdf(it["url"], dest)
            count = len(PdfReader(dest).pages)
        except Exception as e:
            print(f"[warn] Skipping '{it['title']}' due to download/read error: {e}", file=sys.stderr)
            continue
        cache_paths.append((it, dest))
        page_counts.append(count)

    # 2) Compute page mapping
    ABS_OFFSET = 2  # cover + contents occupy first two pages
    page_map = []
    body_cursor = 1
    abs_cursor = ABS_OFFSET + 1
    for (it, _), count in zip(cache_paths, page_counts):
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

    # 3) Assemble the document
    writer = PdfWriter()

    # Cover
    cover_pdf = make_cover_pdf(cover, pagesize=A4)
    writer.add_page(cover_pdf.pages[0])
    add_bookmark(writer, "Cover", 0)

    # Contents
    toc_entries = [(ent["title"], ent["start_body"], ent["start_abs"]) for ent in page_map]
    toc_pdf, toc_link_rects = make_contents_pdf(toc_entries, pagesize=A4)
    writer.add_page(toc_pdf.pages[0])
    add_bookmark(writer, "Contents", 1)

    # Body with bookmarks and footer numbering
    section_parent = add_bookmark(writer, "Sections", 2)
    abs_page_index = 2  # where body starts, zero-based

    for (it, src_path), meta in zip(cache_paths, page_map):
        section_start = abs_page_index
        add_bookmark(writer, it["title"], section_start, parent=section_parent)

        src = PdfReader(src_path)
        body_page_num = meta["start_body"]
        for pg in src.pages:
            w, h = float(pg.mediabox.width), float(pg.mediabox.height)
            overlay = page_number_overlay(w, h, f"{body_page_num}")
            merge_page_safe(pg, overlay)
            writer.add_page(pg)
            abs_page_index += 1
            body_page_num += 1

    # 4) Make ToC entries clickable (best effort)
    toc_index = 1  # Contents page is second page (zero-based index 1)
    for rect, target_index in toc_link_rects:
        add_internal_link(writer, toc_index, target_index, rect)

    # 5) Write out
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    with open(MASTER, "wb") as f:
        writer.write(f)

    return page_map

# ----------------------------- README update -----------------------------

def update_readme(md: str, page_map: list[dict]) -> str:
    size = human_size(MASTER.stat().st_size) if MASTER.exists() else "0 B"
    updated = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        BEGIN_MARK,
        "",
        "## Master PDF",
        f"- 📘 **[Download Master PDF](pdfs/{MASTER.name})** ({size})",
        "- _Cover and Contents pages are unnumbered; body pages start at 1._",
        f"- _Last updated: {updated}_",
        "",
        "### Page map (body numbering)",
    ]
    if not page_map:
        lines.append("- *(no PDFs found in README)*")
    else:
        for ent in page_map:
            # Link uses absolute page index so viewers open the correct target
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

# ----------------------------- main -----------------------------

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
