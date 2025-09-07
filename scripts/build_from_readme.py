import io, re, os, sys, datetime, textwrap
from pathlib import Path

# Prefer PyPDF2 (has add_link). Fallback to pypdf if present.
try:
    from PyPDF2 import PdfReader, PdfWriter
    from PyPDF2.generic import DictionaryObject, NameObject, ArrayObject, FloatObject, NumberObject
    PYPDF_BACKEND = "PyPDF2"
except Exception:
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, ArrayObject, FloatObject, NumberObject
    PYPDF_BACKEND = "pypdf"

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
CACHE.mkdir(exist_ok=True)

# ---------- small helpers ----------

def human_size(n):
    for u in ("B","KB","MB","GB","TB"):
        if n < 1024 or u == "TB":
            return f"{n:.1f} {u}"
        n /= 1024

def strip_md_inline(s: str) -> str:
    s = re.sub(r"\*\*(.*?)\*\*", r"\1", s)
    s = re.sub(r"\*(.*?)\*", r"\1", s)
    s = re.sub(r"`(.*?)`", r"\1", s)
    s = re.sub(r"\[(.*?)\]\((.*?)\)", r"\1", s)
    return s

def read_file(p: Path) -> str:
    return p.read_text(encoding="utf-8")

def write_file(p: Path, s: str):
    p.write_text(s, encoding="utf-8")

# ---------- README parsing ----------

def parse_readme(md: str):
    lines = md.splitlines()

    # H1 title
    title = None
    for ln in lines:
        m = re.match(r"^\s*#\s+(.*)", ln)
        if m:
            title = strip_md_inline(m.group(1).strip())
            break

    # Top block before first ### or the master markers
    top = []
    for ln in lines:
        if ln.strip() == BEGIN_MARK:
            break
        if re.match(r"^\s*###\s+", ln):
            break
        top.append(ln)

    # Intro text after H1 until '---'
    intro = []
    got_title = False
    for ln in top:
        if not got_title:
            if re.match(r"^\s*#\s+", ln):
                got_title = True
            continue
        if ln.strip().startswith("---"):
            break
        intro.append(ln)
    intro_text = strip_md_inline("\n".join(intro).strip())

    # Majors list between '## Major Subjects' and next '---'
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
        m = re.search(r"\[(.+?)\]\((https?://[^\s)]+\.pdf)\)", ln, re.I)
        if m:
            survival = {"text": strip_md_inline(m.group(1)), "url": m.group(2)}
            break

    # Section items: ### + "Download PDF" link
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
        "intro": [ln for ln in intro_text.splitlines() if ln.strip()],
        "majors": majors,
        "survival": survival,
    }
    return cover, items

# ---------- PDF generation primitives ----------

def download_pdf(url: str, dest: Path):
    if dest.exists() and dest.stat().st_size > 0:
        return
    with requests.get(url, stream=True, allow_redirects=True) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(65536):
                if chunk:
                    f.write(chunk)

def make_cover_pdf(cover: dict, pagesize=A4) -> PdfReader:
    packet = io.BytesIO()
    c = canvas.Canvas(packet, pagesize=pagesize)
    W, H = pagesize
    margin = 1.0 * inch
    y = H - 1.4 * inch

    c.setFont("Helvetica-Bold", 24)
    c.drawString(margin, y, cover["title"])
    y -= 0.5 * inch

    c.setFont("Helvetica", 12)
    intro = "\n".join(cover.get("intro") or [])
    for para in [p for p in intro.split("\n") if p.strip()]:
        for line in textwrap.wrap(para, width=90):
            c.drawString(margin, y, line); y -= 14
        y -= 6

    majors = cover.get("majors") or []
    if majors:
        y -= 10
        c.setFont("Helvetica-Bold", 14); c.drawString(margin, y, "Major Subjects in the Programme"); y -= 18
        c.setFont("Helvetica", 12)
        for m in majors:
            c.drawString(margin + 14, y, f"• {m}"); y -= 14

    surv = cover.get("survival")
    if surv:
        y -= 20
        c.setFont("Helvetica", 12)
        label = f"Helpful: {surv['text']}"
        c.drawString(margin, y, label)
        tw = c.stringWidth(label, "Helvetica", 12)
        c.linkURL(surv["url"], (margin, y-2, margin+tw, y+12), relative=0)

    c.setFont("Helvetica", 9)
    c.drawRightString(W - margin, 0.6 * inch, "Generated from README.md")

    c.showPage(); c.save(); packet.seek(0)
    return PdfReader(packet)

def make_contents_pdf(entries, pagesize=A4):
    """Returns (toc_pdf_reader, link_rects). link_rects: [(rect, body_start, abs_start)]"""
    packet = io.BytesIO()
    c = canvas.Canvas(packet, pagesize=pagesize)
    W, H = pagesize
    margin = 1.0 * inch
    y = H - 1.2 * inch

    c.setFont("Helvetica-Bold", 20); c.drawString(margin, y, "Contents"); y -= 0.35 * inch
    c.setFont("Helvetica", 12)

    link_rects = []
    for title, body_start, abs_start in entries:
        line = textwrap.shorten(title, width=100, placeholder="…")
        c.drawString(margin, y, line)
        c.drawRightString(W - margin, y, f"{body_start}")
        rect = (margin, y-2, W - margin, y+12)
        link_rects.append((rect, body_start, abs_start))
        y -= 16
        if y < 1.0 * inch:
            break

    c.showPage(); c.save(); packet.seek(0)
    return PdfReader(packet), link_rects

def page_number_overlay(width: float, height: float, text: str):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(width, height))
    c.setFont("Helvetica", 9)
    c.drawRightString(width - 36, 18, text)
    c.save(); buf.seek(0)
    return PdfReader(buf).pages[0]

# ---------- compatibility helpers ----------

def add_bookmark(writer: PdfWriter, title: str, page_index: int, parent=None):
    # PyPDF2 >= 3: add_outline_item ; PyPDF2 < 3: addBookmark ; pypdf: add_outline_item
    if hasattr(writer, "add_outline_item"):
        return writer.add_outline_item(title, page_index, parent=parent)
    if hasattr(writer, "addBookmark"):
        return writer.addBookmark(title, page_index, parent)
    # No-op fallback
    return None

def add_internal_link(writer: PdfWriter, from_page: int, to_page: int, rect):
    # Try high-level APIs first
    if hasattr(writer, "add_link"):
        writer.add_link(from_page, to_page, rect)
        return
    if hasattr(writer, "addLink"):
        writer.addLink(from_page, to_page, rect)
        return
    # Fallback: attempt a low-level link annotation (may not work on all backends)
    try:
        page = writer.pages[from_page]
        dest_page = writer.pages[to_page]
        dest = ArrayObject([dest_page.indirect_reference, NameObject("/Fit")])  # pypdf
        annot = DictionaryObject()
        annot.update({
            NameObject("/Type"): NameObject("/Annot"),
            NameObject("/Subtype"): NameObject("/Link"),
            NameObject("/Rect"): ArrayObject([FloatObject(rect[0]), FloatObject(rect[1]), FloatObject(rect[2]), FloatObject(rect[3])]),
            NameObject("/Border"): ArrayObject([NumberObject(0), NumberObject(0), NumberObject(0)]),
            NameObject("/Dest"): dest,
        })
        # Attach annotation
        if "/Annots" in page:
            page["/Annots"].append(annot)
        else:
            page[NameObject("/Annots")] = ArrayObject([annot])
    except Exception:
        # As a last resort, skip the link
        pass

# ---------- assembly ----------

def build_master(cover, items):
    # 1) Download sources and get page counts
    cache_paths, page_counts = [], []
    for idx, it in enumerate(items):
        dest = CACHE / f"src_{idx:03d}.pdf"
        download_pdf(it["url"], dest)
        cache_paths.append(dest)
        page_counts.append(len(PdfReader(dest).pages))

    # Numbering plan
    ABS_OFFSET = 2  # cover + contents
    page_map, body_cursor, abs_cursor = [], 1, ABS_OFFSET + 1
    for it, count in zip(items, page_counts):
        start_body, end_body = body_cursor, body_cursor + count - 1
        start_abs, end_abs = abs_cursor, abs_cursor + count - 1
        page_map.append({
            "title": it["title"],
            "start_body": start_body, "end_body": end_body,
            "start_abs": start_abs,   "end_abs": end_abs,
        })
        body_cursor += count; abs_cursor += count

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

    # Body
    section_parent = add_bookmark(writer, "Sections", 2)
    abs_page_index = 2  # zero-based index where body starts
    for it, src_path, meta in zip(items, cache_paths, page_map):
        section_start = abs_page_index
        add_bookmark(writer, it["title"], section_start, parent=section_parent)
        src = PdfReader(src_path)
        body_page_num = meta["start_body"]
        for pg in src.pages:
            w, h = float(pg.mediabox.width), float(pg.mediabox.height)
            overlay = page_number_overlay(w, h, f"{body_page_num}")
            try:
                pg.merge_page(overlay)
            except Exception:
                pass
            writer.add_page(pg)
            abs_page_index += 1
            body_page_num += 1

    # ToC links (try to make them clickable)
    toc_index = 1  # zero-based
    for rect, body_start, abs_start in toc_link_rects:
        add_internal_link(writer, toc_index, abs_start - 1, rect)

    with open(MASTER, "wb") as f:
        writer.write(f)

    return page_map

# ---------- README update ----------

def update_readme(md: str, page_map):
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
            lines.append(
                f"- **{ent['title']}** — pp. {ent['start_body']}–{ent['end_body']} "
                f"(open: [p.{ent['start_body']}](pdfs/{MASTER.name}#page={ent['start_abs']}))"
            )
    lines += ["", END_MARK]
    block = "\n".join(lines)

    if BEGIN_MARK in md and END_MARK in md:
        new_md = re.sub(rf"{re.escape(BEGIN_MARK)}.*?{re.escape(END_MARK)}", block, md, flags=re.DOTALL)
    else:
        sep = "\n\n---\n\n" if not md.endswith("\n") else "\n---\n\n"
        new_md = md + sep + block + "\n"
    return new_md

# ---------- main ----------

def main():
    if not README.exists():
        print("README.md missing", file=sys.stderr); sys.exit(1)
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
