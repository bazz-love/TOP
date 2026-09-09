"""Render catalog pages in the v12 chrome: 2×4 grid, column-major, growing line blocks."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from pathlib import Path

import pymupdf
from PIL import Image

from catalog.fonts import SEGOE_BOLD, SEGOE_REG, ensure_segoe_fonts
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
LABEL = (158 / 255, 153 / 255, 148 / 255)  # #9e9994
INK = (28 / 255, 26 / 255, 23 / 255)  # #1c1a17
ORANGE = (1.0, 0.4, 0.0)
WHITE = (1, 1, 1)

FONT_REG = str(SEGOE_REG)
FONT_BOLD = str(SEGOE_BOLD)

# v12 category strips left of each column
STRIP_X = ((19.84, 30.47), (304.72, 315.35))
STRIP_FILL = (0.965, 0.965, 0.968)
STRIP_STROKE = (0.90, 0.90, 0.92)


@dataclass
class Placed:
    line: ProductLine
    col: int
    row: int
    slots: int


def remaining_lines(lines: list[ProductLine]) -> list[ProductLine]:
    return list(lines)


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
    tokens = re.findall(r"\([^()]*(?:\([^()]*\)[^()]*)*\)|\S+", text) or [text]
    lines: list[str] = []
    cur = ""
    for w in tokens:
        trial = (cur + " " + w).strip()
        if font.text_length(trial, fontsize=size) <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
            if font.text_length(cur, fontsize=size) > max_w:
                # still too long: split on spaces inside
                bits = w.split()
                cur = ""
                for b in bits:
                    trial = (cur + " " + b).strip()
                    if cur and font.text_length(trial, fontsize=size) > max_w:
                        lines.append(cur)
                        cur = b
                    else:
                        cur = trial
    if cur:
        lines.append(cur)
    return lines or [text]


def _sort_variants(products: list[Product]) -> list[Product]:
    def key(p: Product):
        sizes = [int(x) for x in re.findall(r"(\d+)\s*мм", p.name)]
        return (sizes[-1] if sizes else 10_000, p.sku)

    return sorted(products, key=key)


PACK_RE = re.compile(r"\s+\d+\s*/\s*\d+\s*$")
SIZE_RE = re.compile(
    r"(\d+(?:[.,]\d+)?(?:\s*[xх]\s*\d+(?:[.,]\d+)?)?\s*мм)",
    re.I,
)
THREAD_RE = re.compile(r"\b[МM]\s*14\b")
GRIT_RE = re.compile(r"\bP\s*\d+\b", re.I)

# v12 vertical labels: bbox x0 = 22.58 / 307.46, dir=(0,-1), size 5.2
_LABEL_INSERT_X = (26.613, 311.493)
_PAGE_NUM_POS = {
    True: (556.14, 824.22),
    False: (20.73, 824.22),
}


def _normalize_name(name: str) -> str:
    s = (
        name.replace("\u2010", "-")
        .replace("\u2011", "-")
        .replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("\uFD3E", "(")
        .replace("\uFD3F", ")")
    )
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\(\s*", "(", s)
    s = re.sub(r"\s*\)", ")", s)
    s = re.sub(r";\s*", "; ", s)
    return s


def _split_core_and_attrs(name: str) -> tuple[str, dict]:
    s = PACK_RE.sub("", _normalize_name(name)).strip()
    attrs: dict = {}
    if THREAD_RE.search(s):
        attrs["thread"] = "М14"
        s = THREAD_RE.sub(" ", s)
    sizes = SIZE_RE.findall(s)
    if sizes:
        attrs["sizes"] = tuple(
            re.sub(r"\s*мм$", " мм", re.sub(r"\s+", " ", z.strip()), flags=re.I)
            for z in sizes
        )
        s = SIZE_RE.sub(" ", s)
    grit = GRIT_RE.findall(s)
    if grit:
        attrs["grit"] = tuple(g.replace(" ", "").upper() for g in grit)
        s = GRIT_RE.sub(" ", s)
    for hard in ("жесткая", "мягкая"):
        if re.search(hard, s, re.I):
            attrs["hardness"] = hard
            s = re.sub(hard, " ", s, flags=re.I)
    s = re.sub(r"[\(\);,]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" ,;")
    return s, attrs


def _inject_bit(title: str, bit: str) -> str:
    if bit in title:
        return title
    if "для УШМ" in title:
        return title.replace("для УШМ", f"для УШМ, {bit},", 1)
    if "для дрели" in title:
        return title.replace("для дрели", f"для дрели, {bit},", 1)
    return f"{title}, {bit}"


def _tidy_title(title: str) -> str:
    title = re.sub(r"\s+,", ",", title)
    title = re.sub(r",\s*,", ",", title)
    title = re.sub(r"\s+", " ", title)
    return title.strip(" ,;")


def _common_title(products: list[Product]) -> str:
    if len(products) <= 1:
        return ""
    cores_attrs = [_split_core_and_attrs(p.name) for p in products]
    cores = [c for c, _ in cores_attrs]
    word_lists = [c.split() for c in cores]
    prefix: list[str] = []
    for toks in zip(*word_lists):
        if len({t.lower() for t in toks}) == 1:
            prefix.append(toks[0])
        else:
            break
    title = " ".join(prefix).strip(" ,;")
    if len(title) < 12:
        title = min(cores, key=len)
    return _tidy_title(title)


def _variant_label(p: Product, siblings: list[Product], title: str) -> str:
    if len(siblings) == 1:
        return _normalize_name(p.name)
    all_attrs = [_split_core_and_attrs(s.name)[1] for s in siblings]
    mine = _split_core_and_attrs(p.name)[1]
    bits: list[str] = []

    size_vals = [a.get("sizes") for a in all_attrs]
    if len(set(size_vals)) > 1:
        mine_sizes = mine.get("sizes") or ()
        present = [v for v in size_vals if v]
        if present and len({len(v) for v in present}) == 1:
            n = len(present[0])
            varying = [
                i
                for i in range(n)
                if len({v[i] for v in present}) > 1
            ]
            if varying:
                bits.extend(mine_sizes[i] for i in varying if i < len(mine_sizes))
            else:
                bits.extend(mine_sizes)
        else:
            bits.extend(mine_sizes)

    for key in ("thread", "hardness"):
        vals = [a.get(key) for a in all_attrs]
        if len(set(vals)) > 1 and mine.get(key):
            bits.append(mine[key])
    grit_vals = [a.get("grit") for a in all_attrs]
    if len(set(grit_vals)) > 1 and mine.get("grit"):
        bits.extend(mine["grit"])

    return ", ".join(bits) if bits else _normalize_name(p.name)


def _stamp_chrome(page: pymupdf.Page, src: pymupdf.Document, odd: bool, number: str):
    """Vector-copy v12 header/footer; wipe only the product band, keep chrome."""
    tmpl = 0 if odd else 1
    tmp = pymupdf.open()
    tmp.insert_pdf(src, from_page=tmpl, to_page=tmpl)
    # Inset so the header line at 90.7 and footer at 796.5 stay untouched.
    tmp[0].add_redact_annot(pymupdf.Rect(0, 92.0, PAGE_W, 795.5), fill=WHITE)
    if odd:
        tmp[0].add_redact_annot(pymupdf.Rect(554.0, 809.5, 577.0, 829.5), fill=ORANGE)
    else:
        tmp[0].add_redact_annot(pymupdf.Rect(18.5, 809.5, 41.5, 829.5), fill=ORANGE)
    tmp[0].apply_redactions(images=0)
    page.show_pdf_page(page.rect, tmp, 0)
    tmp.close()
    page.insert_font(fontname="segoeb", fontfile=FONT_BOLD)
    x, y = _PAGE_NUM_POS[odd]
    # Cover the template digits only — keep the diagonal orange number block.
    if odd:
        page.draw_rect(
            pymupdf.Rect(554.0, 809.5, 577.0, 829.5),
            color=ORANGE,
            fill=ORANGE,
            width=0,
        )
    else:
        page.draw_rect(
            pymupdf.Rect(18.5, 809.5, 41.5, 829.5),
            color=ORANGE,
            fill=ORANGE,
            width=0,
        )
    page.insert_text(
        (x, y),
        number,
        fontname="segoeb",
        fontsize=16,
        color=WHITE,
    )


def _place_image(page: pymupdf.Page, image_png: bytes | None, dest: pymupdf.Rect):
    if not image_png or dest.width < 8 or dest.height < 8:
        return
    pix = pymupdf.Pixmap(image_png)
    iw, ih = pix.width, pix.height
    if not iw or not ih:
        return
    scale = min(dest.width / iw, dest.height / ih)
    dw, dh = iw * scale, ih * scale
    box = pymupdf.Rect(
        dest.x0 + (dest.width - dw) / 2,
        dest.y0 + (dest.height - dh) / 2,
        dest.x0 + (dest.width - dw) / 2 + dw,
        dest.y0 + (dest.height - dh) / 2 + dh,
    )
    page.insert_image(box, pixmap=pix)


def _draw_text_row(
    page: pymupdf.Page,
    font_r: pymupdf.Font,
    y: float,
    name_x: float,
    sku_r: float,
    price_r: float,
    name: str,
    sku: str | None,
    price: str | None,
):
    page.insert_text(
        (name_x, y + 7.2),
        name,
        fontname="segoe",
        fontsize=7.2,
        color=INK,
    )
    if sku:
        sku_w = font_r.text_length(sku, fontsize=7.2)
        page.insert_text(
            (sku_r - sku_w, y + 7.2),
            sku,
            fontname="segoe",
            fontsize=7.2,
            color=INK,
        )
    if price:
        price_w = font_r.text_length(price, fontsize=7.2)
        page.insert_text(
            (price_r - price_w, y + 7.2),
            price,
            fontname="segoe",
            fontsize=7.2,
            color=INK,
        )


def _draw_category_label(
    page: pymupdf.Page, col: int, y0: float, y1: float, text: str
):
    x0, x1 = STRIP_X[col]
    page.draw_rect(
        pymupdf.Rect(x0, y0, x1, y1),
        color=STRIP_STROKE,
        fill=STRIP_FILL,
        width=0.35,
        radius=0.09,
    )
    page.insert_font(fontname="segoe", fontfile=FONT_REG)
    font = pymupdf.Font(fontfile=FONT_REG)
    size = 5.2
    tw = font.text_length(text, fontsize=size)
    page.insert_text(
        (_LABEL_INSERT_X[col], (y0 + y1) / 2 + tw / 2),
        text,
        fontname="segoe",
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
    page.insert_font(fontname="segoe", fontfile=FONT_REG)
    rect = _card_rect(placed.col, placed.row, placed.slots)
    page.draw_rect(rect, color=STROKE, fill=WHITE, width=0.65, radius=0.025)

    products = list(placed.line.products)
    n = len(products)
    if n >= 3:
        products = _sort_variants(products)
    name_x = rect.x0 + 7.0
    price_r = rect.x1 - 7.0
    sku_r = rect.x0 + 205.6
    row_h = 13.5
    pad_bottom = 6.0

    if n <= 2:
        wraps = [
            _wrap(font_r, _normalize_name(p.name), 7.2, max(40, sku_r - name_x - 36))[:3]
            for p in products
        ]
        text_h = sum(max(1, len(w)) * row_h for w in wraps)
        text_top = rect.y1 - pad_bottom - text_h
        img_rect = pymupdf.Rect(rect.x0 + 10, rect.y0 + 8, rect.x1 - 10, text_top - 4)
        _place_image(page, image_png, img_rect)
        y = text_top
        for p, lines in zip(products, wraps):
            for i, ln in enumerate(lines):
                _draw_text_row(
                    page,
                    font_r,
                    y,
                    name_x,
                    sku_r,
                    price_r,
                    ln,
                    p.sku if i == 0 else None,
                    p.price if i == 0 else None,
                )
                y += row_h
        return

    title = _common_title(products)
    title_lines = _wrap(font_r, title, 7.2, rect.width - 16)[:2] if title else []
    title_h = row_h * len(title_lines)
    title_top = rect.y0 + 8
    y = title_top
    for tl in title_lines:
        page.insert_text(
            (name_x, y + 7.2),
            tl,
            fontname="segoe",
            fontsize=7.2,
            color=INK,
        )
        y += row_h
    free = pymupdf.Rect(rect.x0 + 6, y + 2, rect.x1 - 6, rect.y1 - pad_bottom)

    labels = [_variant_label(p, products, title) for p in products]

    if n <= 7:
        mid = (free.x0 + free.x1) / 2
        img_rect = pymupdf.Rect(free.x0, free.y0, mid - 3, free.y1)
        _place_image(page, image_png, img_rect)
        var_x = mid + 2
        max_sku_w = max(font_r.text_length(p.sku, fontsize=7.2) for p in products)
        max_price_w = max(font_r.text_length(p.price, fontsize=7.2) for p in products)
        var_price = free.x1
        var_sku = var_price - max_price_w - 6
        name_w = max(24, var_sku - max_sku_w - 6 - var_x)
        avail = max(1, free.height)
        vh = min(row_h, avail / max(1, n))
        yv = free.y0 + max(0, (avail - n * vh) / 2)
        for p, lab in zip(products, labels):
            shown = _wrap(font_r, lab, 7.2, name_w)[0]
            _draw_text_row(
                page, font_r, yv, var_x, var_sku, var_price, shown, p.sku, p.price
            )
            yv += vh
        return

    # 8+: title, medium image, then variant list
    img_h = min(free.height * 0.42, 120)
    img_rect = pymupdf.Rect(free.x0 + 20, free.y0, free.x1 - 20, free.y0 + img_h)
    _place_image(page, image_png, img_rect)
    list_top = img_rect.y1 + 4
    avail = max(8, free.y1 - list_top)
    vh = min(row_h, avail / max(1, n))
    yv = list_top
    for p, lab in zip(products, labels):
        shown = _wrap(font_r, lab, 7.2, max(40, sku_r - name_x - 36))[0]
        _draw_text_row(page, font_r, yv, name_x, sku_r, price_r, shown, p.sku, p.price)
        yv += vh


def render_new_pages(
    catalog_src: Path,
    price_src: Path,
    lines: list[ProductLine],
    out_path: Path,
    n_new: int = 4,
) -> list[list[Placed]]:
    ensure_segoe_fonts()
    src = pymupdf.open(catalog_src)
    price = pymupdf.open(price_src)
    out = pymupdf.open()

    pages = place_pages(remaining_lines(lines), n_new)
    font_r = pymupdf.Font(fontfile=FONT_REG)
    img_cache: dict[tuple[int | None, int | None], bytes | None] = {}

    start_num = 1
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
