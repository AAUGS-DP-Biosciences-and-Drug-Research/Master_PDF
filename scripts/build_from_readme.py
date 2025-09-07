import io, re, os, sys, datetime
from pathlib import Path
import requests
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
PDF_DIR = ROOT / "pdfs"
MASTER = PDF_DIR / "master.pdf"

BEGIN_MARK = "<!-- BEGIN MASTER INDEX -->"
END_MARK   = "<!-- END MASTER INDEX -->"

PDF_DIR.mkdir(parents=True, exist_ok=True)

def human_size(n):
    for u in ("B","KB","MB","GB","TB"):
        if n < 1024 or u == "TB":
            return f"{n:.1f} {u}"
        n /= 1024

def parse_readme(md: str):
    """
    Extract (title, url) pairs in the order they appear.
    Title = nearest preceding '### ' heading.
    URL = a markdown link on the same line as 'Download PDF'.
    """
    items = []
    current_h3 = None
    for line in md.splitlines():
        h = re.match(r"^\s*###\s+(.*)\s*$", line)
        if h:
            current_h3 = h.group(1).strip()
            continue
        m = re.search(r"\[.*?Download PDF.*?\]\((https?://[^)]+?\.pdf)\)", line, re.IGNORECASE)
        if m:
            url = m.group(1).strip()
            title = current_h3 or "Document"
            items.append({"title": title, "url": url})
    return items

def download_pdf(url: str, dest: Path):
    with requests.get(url, stream=True, allow_redirects=True) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(65536):
                if chunk:
                    f.write(chunk)

def page_number_overlay(width: float, height: float, text: str):
    # Build a single-page PDF in-memory with the page number, then return it as a pypdf page.
    packet = io.BytesIO()
    c = canvas.Canvas(packet, pagesize=(width, height))
    c.setFont("Helvetica", 9)
    # bottom-right, with a small margin
    c.drawRightString(width - 36, 18, text)
    c.save()
    packet.seek(0)
    return PdfReader(packet).pages[0]

def build_master(items):
    """
    Merge all PDFs into MASTER, add page numbers and bookmarks.
    Return a list of dicts: [{title, start, end}], 1-based inclusive.
    """
    writer = PdfWriter()
    page_map = []
    page_no = 1

    tmp_dir = ROOT / ".cache"
    tmp_dir.mkdir(exist_ok=True)

    for idx, it in enumerate(items):
        tmp_pdf = tmp_dir / f"src_{idx:03d}.pdf"
        download_pdf(it["url"], tmp_pdf)

        src = PdfReader(tmp_pdf)
        start = page_no
        # Bookmark at the section start
        writer.add_outline_item(it["title"], page_no - 1)

        for i, pg in enumerate(src.pages):
            width = float(pg.mediabox.width)
            height = float(pg.mediabox.height)
            # Overlay global page number
            overlay = page_number_overlay(width, height, f"{page_no}")
            try:
                pg.merge_page(overlay)  # pypdf
            except Exception:
                # fallback: some pages may be xobjects-only; ignore numbering if merge fails
                pass
            writer.add_page(pg)
            page_no += 1

        page_map.append({"title": it["title"], "start": start, "end": page_no - 1})

    PDF_DIR.mkdir(exist_ok=True)
    with open(MASTER, "wb") as f:
        writer.write(f)

    return page_map

def update_readme(md: str, page_map):
    size = human_size(MASTER.stat().st_size) if MASTER.exists() else "0 B"
    updated = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    block_lines = []
    block_lines.append(BEGIN_MARK)
    block_lines.append("")
    block_lines.append("## Master PDF")
    block_lines.append(f"- 📘 **[Download Master PDF](pdfs/{MASTER.name})** ({size})")
    block_lines.append(f"- _Built from the sections above, in order. Last updated: {updated}_")
    block_lines.append("")
    block_lines.append("### Page map")
    if not page_map:
        block_lines.append("- *(no PDFs found in README)*")
    else:
        for ent in page_map:
            block_lines.append(
                f"- **{ent['title']}** — pp. {ent['start']}–{ent['end']} "
                f"([open at p.{ent['start']}](pdfs/{MASTER.name}#page={ent['start']}))"
            )
    block_lines.append("")
    block_lines.append(END_MARK)
    block = "\n".join(block_lines)

    if BEGIN_MARK in md and END_MARK in md:
        # replace existing block
        new_md = re.sub(
            rf"{re.escape(BEGIN_MARK)}.*?{re.escape(END_MARK)}",
            block,
            md,
            flags=re.DOTALL,
        )
    else:
        # append a divider + block
        sep = "\n\n---\n\n" if not md.endswith("\n") else "\n---\n\n"
        new_md = md + sep + block + "\n"
    return new_md

def main():
    md = README.read_text(encoding="utf-8")
    items = parse_readme(md)

    if not items:
        print("No 'Download PDF' links found in README. Nothing to do.", file=sys.stderr)
        return

    page_map = build_master(items)
    new_md = update_readme(md, page_map)
    if new_md != md:
        README.write_text(new_md, encoding="utf-8")

if __name__ == "__main__":
    main()
