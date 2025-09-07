import io, re, os, sys, datetime, textwrap
from pathlib import Path
import requests
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader

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
    # very light markdown cleanup for cover text
    s = re.sub(r"\*\*(.*?)\*\*", r"\1", s)             # **bold**
    s = re.sub(r"\*(.*?)\*", r"\1", s)                 # *italic*
    s = re.sub(r"`(.*?)`", r"\1", s)                   # `code`
    s = re.sub(r"\[(.*?)\]\((.*?)\)", r"\1", s)        # [text](link) -> text
    return s

def read_file(p: Path) -> str:
    return p.read_text(encoding="utf-8")

def write_file(p: Path, s: str):
    p.write_text(s, encoding="utf-8")

# ---------- README parsing ----------

def parse_readme(md: str):
    """
    Returns:
      cover = {
        "title": str,
        "intro": [lines],
        "majors": [items] (optional),
        "survival": {"text": "...", "url": "..."} (optional)
      }
      items = [{"title": "...", "url": "https://...pdf"}]  # in order
    """
    lines = md.splitlines()

    # Title: first H1
    title = None
    for ln in lines:
        m = re.match(r"^\s*#\s+(.*)", ln)
        if m:
            title = strip_md_inline(m.group(1).strip())
            break

    # Capture "top" block before first '###' (section) or our master markers
    top = []
    for ln in lines:
        if ln.strip() == BEGIN_MARK:
            break
        if re.match(r"^\s*###\s+", ln):
            break
        top.append(ln)

    # Intro: first paragraph after H1 (until first '---' or blank block)
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
    intro_text = "\n".join(intro).strip()
    intro_text = strip_md_inline(intro_text)

    # Major subjects: block between '## Major Subjects' and next '---'
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

    # Survival guide link (first explicit PDF link in top block)
    survival = None
    for ln in top:
        m = re.search(r"\[(.+?)\]\((https?://[^\s)]+\.pdf)\)", ln, re.I)
        if m:
            survival = {"text": strip_md_inline(m.group(1)), "url": m.group(2)}
            break

    # Section items: ### Heading + a line with "Download PDF"
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
    """Create a 1-page cover PDF from README top content."""
    packet = io.BytesIO()
    c = canvas.Canvas(packet, pagesize=pagesize)
    W, H = pagesize
    margin = 1.0 * inch
    y = H - 1.4 * inch

    # Title
    c.setFont("Helvetica-Bold", 24)
    c.drawString(margin, y, cover["title"])
    y -= 0.5 * inch

    # Intro
    c.setFont("Helvetica", 12)
    intro = "\n".join(cover.get("intro") or [])
    for para in [p for p in intro.split("\n") if p.strip()]:
        for line in textwrap.wrap(para, width=90):
            c.drawString(margin, y, line)
            y -= 14
        y -= 6

    # Majors (if any)
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

    # Survival Guide link
    surv = cover.get("survival")
    if surv:
        y -= 20
        c.setFont("Helvetica", 12)
        label = f"Helpful: {surv['text']}"
        c.drawString(margin, y, label)
        # Clickable link rectangle
        text_w = c.stringWidth(label, "Helvetica", 12)
        c.linkURL(surv["url"], (margin, y-2, margin + text_w, y+12), relative=0)

    # Footer (programme name small)
    c.setFont("Helvetica", 9)
    c.drawRightString(W - margin, 0.6 * inch, "Generated from README.md")

    c.showPage()
    c.save()
    packet.seek(0)
    return PdfReader(packet)

def make_contents_pdf(entries, pagesize=A4) -> tuple[PdfReader, list]:
    """Create a 1-page Contents PDF listing section titles + body page numbers."""
    packet = io.BytesIO()
    c = canvas.Canvas(packet, pagesize=pagesize)
    W, H = pagesize
    margin = 1.0 * inch
    y = H - 1.2 * inch

    c.setFont("Helvetica-Bold", 20)
    c.drawString(margin, y, "Contents")
    y -= 0.35 * inch

    c.setFont("Helvetica", 12)
    link_rects = []  # [(x1,y1,x2,y2), body_start, abs_page_dest]
    for title, body_start, abs_start in entries:
        # dot leader to page number
        text = title
        page_str = f"{body_start}"
        max_text_width = W - margin*2 - 40
        # wrap long titles
        wrapped = textwrap.wrap(text, width=90)
        # only show the first line in ToC (simple)
        line = wrapped[0] if wrapped else text
        truncated = line
        # compute measure and draw
        c.drawString(margin, y, truncated)
        c.drawRightString(W - margin, y, page_str)
        # Add internal link rect for this line (we'll attach with pypdf later)
        rect = (margin, y-2, W - margin, y+12)
        link_rects.append((rect, body_start, abs_start))
        y -= 16
        if y < 1.0 * inch:
            break  # keep ToC to a single page for simplicity

    c.showPage()
    c.save()
    packet.seek(0)
    return PdfReader(packet), link_rects

def page_number_overlay(width: float, height: float, text: str):
    """Return a single-page PDF with just the page number text (to overlay)."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(width, height))
    c.setFont("Helvetica", 9)
    c.drawRightString(width - 36, 18, text)
    c.save()
    buf.seek(0)
    return PdfReader(buf).pages[0]

# ---------- assembly ----------

def build_master(cover, items):
    """Create master.pdf with:
       - cover (no footer number)
       - contents (no footer number, clickable)
       - merged body (footer numbers starting at 1), with bookmarks
       Returns page_map for README.
    """
    # 1) Download + size each source to compute page_map
    cache_paths = []
    page_counts = []
    for idx, it in enumerate(items):
        dest = CACHE / f"src_{idx:03d}.pdf"
        download_pdf(it["url"], dest)
        cache_paths.append(dest)
        page_counts.append(len(PdfReader(dest).pages))

    # Body numbering starts at 1; cover and contents come before body.
    ABS_OFFSET = 2  # cover(1) + contents(2)
    page_map = []
    body_cursor = 1
    abs_cursor = ABS_OFFSET + 1  # first body page absolute index

    for it, count in zip(items, page_counts):
        start_body = body_cursor
        end_body = body_cursor + count - 1
        start_abs = abs_cursor
        end_abs = abs_cursor + count - 1
        page_map.append({
            "title": it["title"],
            "start_body": start_body,
            "end_body": end_body,
            "start_abs": start_abs,
            "end_abs": end_abs,
        })
        body_cursor += count
        abs_cursor += count

    # 2) Build actual pages
    writer = PdfWriter()

    # Cover
    cover_pdf = make_cover_pdf(cover, pagesize=A4)
    writer.add_page(cover_pdf.pages[0])
    cover_outline = writer.add_outline_item("Cover", 0)

    # Contents (we need body page_map to render body page numbers; links added later)
    toc_entries = [(ent["title"], ent["start_body"], ent["start_abs"]) for ent in page_map]
    toc_pdf, toc_link_rects = make_contents_pdf(toc_entries, pagesize=A4)
    writer.add_page(toc_pdf.pages[0])
    contents_outline = writer.add_outline_item("Contents", 1)

    # Body: merge each page, add footer page number (body), add bookmarks per section
    section_parent = writer.add_outline_item("Sections", 2)  # parent group for bookmarks
    abs_page_index = 2  # zero-based index in writer where body starts (page #3 in PDF)

    for section_idx, (it, src_path, meta) in enumerate(zip(items, cache_paths, page_map)):
        section_start_index = abs_page_index
        writer.add_outline_item(it["title"], section_start_index, parent=section_parent)

        src = PdfReader(src_path)
        body_page_number = meta["start_body"]

        for pg in src.pages:
            w = float(pg.mediabox.width)
            h = float(pg.mediabox.height)
            # Overlay body page number (arabic, starting at 1)
            overlay = page_number_overlay(w, h, f"{body_page_number}")
            try:
                pg.merge_page(overlay)
            except Exception:
                pass
            writer.add_page(pg)
            abs_page_index += 1
            body_page_number += 1

    # 3) Add clickable links on the ToC page (created earlier)
    toc_index = 1  # zero-based
    for rect, body_start, abs_start in toc_link_rects:
        # add_link(from_page, to_page, rect=(x1,y1,x2,y2))
        writer.add_link(toc_index, abs_start - 1, rect)

    # 4) Write master
    PDF_DIR.mkdir(exist_ok=True)
    with open(MASTER, "wb") as f:
        writer.write(f)

    return page_map

# ---------- README update ----------

def update_readme(md: str, page_map):
    size = human_size(MASTER.stat().st_size) if MASTER.exists() else "0 B"
    updated = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    lines = []
    lines.append(BEGIN_MARK)
    lines.append("")
    lines.append("## Master PDF")
    lines.append(f"- 📘 **[Download Master PDF](pdfs/{MASTER.name})** ({size})")
    lines.append("- _Cover and Contents pages are unnumbered footers; body pages start at 1._")
    lines.append(f"- _Last updated: {updated}_")
    lines.append("")
    lines.append("### Page map (body numbering)")
    if not page_map:
        lines.append("- *(no PDFs found in README)*")
    else:
        for ent in page_map:
            lines.append(
                f"- **{ent['title']}** — pp. {ent['start_body']}–{ent['end_body']} "
                f"(open: [p.{ent['start_body']}](pdfs/{MASTER.name}#page={ent['start_abs']}))"
            )
    lines.append("")
    lines.append(END_MARK)
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
