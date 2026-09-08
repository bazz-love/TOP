"""Render catalog pages in the v12 chrome: 2×4 grid, column-major, growing line blocks."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from pathlib import Path

import pymupdf
from PIL import Image

from catalog.parse_price import Product, ProductLine

PAGE_W, PAGE_H = 595.27557, 841.88977
HEADER_H = 90.7
FOOTER_Y = 796.5

COL_X = (33.9, 318.8)
ROW_Y = (99.2, 273.9, 448.6, 623.3)
CELL_W, CELL_H = 256.7, 167.6
ROW_GAP = 7.1
SLOTS_PER_COL = 4

STROKE = (0.82, 0.79, 0.75)
LABEL = (0.62, 0.60, 0.58)
INK = (0.11, 0.10, 0.09)
ORANGE = (1.0, 0.4, 0.0)
WHITE = (1, 1, 1)

FONT_REG = "/usr/share/fonts/truetype/macos/Inter-Regular.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/macos/Inter-Bold.ttf"

ALREADY_IN_CATALOG = {
    "CA550",
    "CA520",
    "AG30-190",
    "GA220",
    "40236",
    "55263",
    "40238",
    "40221",
    "40234",
    "40240",
    "40318",
    "52227",
    "40032",
    "40601",
    "55276",
    "55277",
    "40216",
    "40159",
}


@dataclass
class Placed:
    line: ProductLine
    col: int
    row: int
    slots: int


def remaining_lines(lines: list[ProductLine]) -> list[ProductLine]:
    out = []
    for ln in lines:
        if all(p.sku in ALREADY_IN_CATALOG for p in ln.products):
            continue
        if ln.category_code.startswith("01"):
            continue
        out.append(ln)
    return out


def place_pages(lines: list[ProductLine], n_pages: int) -> list[list[Placed]]:
    pages: list[list[Placed]] = []
    used = [0, 0]
    page: list[Placed] = []

    def new_page():
        nonlocal used, page
        if page:
            pages.append(page)
        page = []
        used = [0, 0]

    for ln in lines:
        if len(pages) >= n_pages:
            break
        slots = min(ln.slots, SLOTS_PER_COL)
        placed = False
        while not placed:
            if used[0] + slots <= SLOTS_PER_COL:
                page.append(Placed(ln, 0, used[0], slots))
                used[0] += slots
                placed = True
            elif used[1] + slots <= SLOTS_PER_COL:
                page.append(Placed(ln, 1, used[1], slots))
                used[1] += slots
                placed = True
            else:
                new_page()
                if len(pages) >= n_pages:
                    break
        if len(pages) >= n_pages and not placed:
            break
        if used[0] == SLOTS_PER_COL and used[1] == SLOTS_PER_COL:
            new_page()
            if len(pages) >= n_pages:
                break

    if page and len(pages) < n_pages:
        pages.append(page)
    return pages[:n_pages]


def _trim_white(png: bytes) -> bytes:
    import numpy as np

    img = Image.open(io.BytesIO(png)).convert("RGB")
    arr = np.asarray(img)
    nonwhite = np.any(arr < 242, axis=2)
    rows = np.where(nonwhite.any(axis=1))[0]
    cols = np.where(nonwhite.any(axis=0))[0]
    if rows.size == 0 or cols.size == 0:
        return png
    pad = 8
    y0 = max(0, int(rows[0]) - pad)
    y1 = min(arr.shape[0], int(rows[-1]) + 1 + pad)
    x0 = max(0, int(cols[0]) - pad)
    x1 = min(arr.shape[1], int(cols[-1]) + 1 + pad)
    cropped = img.crop((x0, y0, x1, y1))
    out = io.BytesIO()
    cropped.save(out, format="PNG")
    return out.getvalue()


def extract_image(price_doc: pymupdf.Document, line: ProductLine) -> bytes | None:
    if line.image_xref is None or line.image_page is None:
        return None
    try:
        pix = pymupdf.Pixmap(price_doc, line.image_xref)
        if pix.n >= 4:
            pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
        png = pix.tobytes("png")
        return _trim_white(png)
    except Exception:
        return None


def _card_rect(col: int, row: int, slots: int) -> pymupdf.Rect:
    x0 = COL_X[col]
    y0 = ROW_Y[row]
    y1 = ROW_Y[row + slots - 1] + CELL_H
    return pymupdf.Rect(x0, y0, x0 + CELL_W, y1)


def _wrap(font: pymupdf.Font, text: str, size: float, max_w: float) -> list[str]:
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        trial = (cur + " " + w).strip()
        if font.text_length(trial, fontsize=size) <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [text]


def _sort_variants(products: list[Product]) -> list[Product]:
    def key(p: Product):
        sizes = [int(x) for x in re.findall(r"(\d+)\s*мм", p.name)]
        return (sizes[-1] if sizes else 10_000, p.sku)

    return sorted(products, key=key)


def _strip_variable_bits(name: str) -> str:
    s = name
    s = re.sub(r"\(\s*М14;?\s*", "(", s)
    s = re.sub(r"М14;?\s*", "", s)
    s = re.sub(r"\d+(?:[.,]\d+)?\s*[xх]\s*\d+(?:[.,]\d+)?\s*мм", "", s, flags=re.I)
    s = re.sub(r"\d+(?:[.,]\d+)?\s*мм", "", s, flags=re.I)
    s = re.sub(r"\(\s*;?\s*\)", "", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip(" ,;()")


def _common_title(products: list[Product]) -> str:
    if len(products) <= 1:
        return ""
    stripped = [_strip_variable_bits(p.name) for p in products]
    words = [s.split() for s in stripped]
    prefix: list[str] = []
    for toks in zip(*words):
        if len({t.lower() for t in toks}) == 1:
            prefix.append(toks[0])
        else:
            break
    title = " ".join(prefix).strip(" ,;")
    title = re.sub(r"\s+", " ", title)
    if len(title) < 12:
        title = max(stripped, key=len)
    return title


def _variant_label(p: Product, siblings: list[Product], title: str) -> str:
    if len(siblings) == 1:
        return p.name
    sizes = re.findall(r"(\d+(?:[.,]\d+)?)\s*мм", p.name, flags=re.I)
    grit = re.search(r"(?:^|[\s,;(])(?:Р|P)(\d+)\b", p.name)
    bits: list[str] = []
    if "М14" in p.name and "М14" not in (title or ""):
        bits.append("М14")
    if sizes:
        bits.append(f"{sizes[-1]} мм")
    if grit:
        bits.append("P" + grit.group(1))
    return ", ".join(bits) if bits else p.name


def _stamp_band(page: pymupdf.Page, src_page: pymupdf.Page, clip: pymupdf.Rect):
    pix = src_page.get_pixmap(matrix=pymupdf.Matrix(3, 3), clip=clip, alpha=False)
    page.insert_image(clip, pixmap=pix)


def _stamp_chrome(page: pymupdf.Page, src: pymupdf.Document, odd: bool, number: str):
    tmpl = src[0 if odd else 1]
    header = pymupdf.Rect(0, 0, PAGE_W, HEADER_H)
    footer = pymupdf.Rect(0, FOOTER_Y, PAGE_W, PAGE_H)
    _stamp_band(page, tmpl, header)
    _stamp_band(page, tmpl, footer)

    page.insert_font(fontname="interb", fontfile=FONT_BOLD)
    if odd:
        box = pymupdf.Rect(507.4, FOOTER_Y, PAGE_W, PAGE_H)
    else:
        box = pymupdf.Rect(0, FOOTER_Y, 87.9, PAGE_H)
    page.draw_rect(box, color=ORANGE, fill=ORANGE, width=0)
    page.insert_textbox(
        box + (0, 8, 0, -6),
        number,
        fontname="interb",
        fontsize=16,
        color=WHITE,
        align=pymupdf.TEXT_ALIGN_CENTER,
    )


def _draw_category_label(
    page: pymupdf.Page, col: int, y0: float, y1: float, text: str
):
    page.insert_font(fontname="inter", fontfile=FONT_REG)
    x = 22.6 if col == 0 else 307.5
    font = pymupdf.Font(fontfile=FONT_REG)
    size = 5.2
    tw = font.text_length(text, fontsize=size)
    mid = (y0 + y1) / 2
    # rotate 90° CCW: text runs upward
    page.insert_text(
        (x + size, mid + tw / 2),
        text,
        fontname="inter",
        fontsize=size,
        color=LABEL,
        rotate=90,
    )


def draw_card(
    page: pymupdf.Page,
    placed: Placed,
    image_png: bytes | None,
    font_r: pymupdf.Font,
):
    page.insert_font(fontname="inter", fontfile=FONT_REG)
    rect = _card_rect(placed.col, placed.row, placed.slots)
    page.draw_rect(rect, color=STROKE, fill=WHITE, width=0.65, radius=0.025)

    products = _sort_variants(placed.line.products)
    name_x = rect.x0 + 7.0
    price_r = rect.x1 - 7.0
    sku_r = rect.x0 + 205.6
    max_sku_w = max(font_r.text_length(p.sku, fontsize=7.2) for p in products)
    name_w = sku_r - max_sku_w - 10 - name_x

    title = _common_title(products)
    row_h = 12.8
    title_lines = (
        _wrap(font_r, title, 6.4, rect.width - 18)[:2] if title else []
    )
    title_h = 11.0 * len(title_lines)
    wraps = [
        _wrap(
            font_r,
            _variant_label(p, products, title),
            7.2,
            max(40, name_w),
        )[: (1 if title else 2)]
        for p in products
    ]
    text_h = title_h + sum(max(1, len(w)) * row_h for w in wraps)
    text_top = rect.y1 - 8 - text_h

    img_rect = pymupdf.Rect(rect.x0 + 10, rect.y0 + 8, rect.x1 - 10, text_top - 4)
    if image_png and img_rect.height > 20:
        pix = pymupdf.Pixmap(image_png)
        iw, ih = pix.width, pix.height
        if iw and ih:
            scale = min(img_rect.width / iw, img_rect.height / ih)
            dw, dh = iw * scale, ih * scale
            dest = pymupdf.Rect(
                img_rect.x0 + (img_rect.width - dw) / 2,
                img_rect.y0 + (img_rect.height - dh) / 2,
                img_rect.x0 + (img_rect.width - dw) / 2 + dw,
                img_rect.y0 + (img_rect.height - dh) / 2 + dh,
            )
            page.insert_image(dest, pixmap=pix)

    y = text_top
    for tl in title_lines:
        page.insert_text(
            (name_x, y + 6.4),
            tl,
            fontname="inter",
            fontsize=6.4,
            color=LABEL,
        )
        y += 11.0
    for p, lines in zip(products, wraps):
        for i, ln in enumerate(lines):
            page.insert_text(
                (name_x, y + 7.0),
                ln,
                fontname="inter",
                fontsize=7.2,
                color=INK,
            )
            if i == 0:
                sku_w = font_r.text_length(p.sku, fontsize=7.2)
                price_w = font_r.text_length(p.price, fontsize=7.2)
                page.insert_text(
                    (sku_r - sku_w, y + 7.0),
                    p.sku,
                    fontname="inter",
                    fontsize=7.2,
                    color=INK,
                )
                page.insert_text(
                    (price_r - price_w, y + 7.0),
                    p.price,
                    fontname="inter",
                    fontsize=7.2,
                    color=INK,
                )
            y += row_h


def render_new_pages(
    catalog_src: Path,
    price_src: Path,
    lines: list[ProductLine],
    out_path: Path,
    n_new: int = 2,
) -> list[list[Placed]]:
    src = pymupdf.open(catalog_src)
    price = pymupdf.open(price_src)
    out = pymupdf.open()
    out.insert_pdf(src)

    pages = place_pages(remaining_lines(lines), n_new)
    font_r = pymupdf.Font(fontfile=FONT_REG)
    img_cache: dict[tuple[int | None, int | None], bytes | None] = {}

    start_num = src.page_count + 1
    for i, placed_list in enumerate(pages):
        page_no = start_num + i
        odd = page_no % 2 == 1
        page = out.new_page(width=PAGE_W, height=PAGE_H)
        _stamp_chrome(page, src, odd, f"{page_no:02d}")

        # category labels: merge consecutive same-category in a column
        for col in (0, 1):
            col_items = [p for p in placed_list if p.col == col]
            col_items.sort(key=lambda p: p.row)
            idx = 0
            while idx < len(col_items):
                j = idx
                cat = col_items[idx].line.category_name
                while j + 1 < len(col_items) and col_items[j + 1].line.category_name == cat:
                    j += 1
                y0 = ROW_Y[col_items[idx].row]
                last = col_items[j]
                y1 = ROW_Y[last.row] + last.slots * CELL_H + (last.slots - 1) * ROW_GAP
                # use actual card bottom
                y1 = _card_rect(col, last.row, last.slots).y1
                _draw_category_label(page, col, y0, y1, cat)
                idx = j + 1

        for pl in placed_list:
            key = (pl.line.image_page, pl.line.image_xref)
            if key not in img_cache:
                img_cache[key] = extract_image(price, pl.line)
            draw_card(page, pl, img_cache[key], font_r)

    out.save(out_path, deflate=True, garbage=4)
    out.close()
    src.close()
    price.close()
    return pages
