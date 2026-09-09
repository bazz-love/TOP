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
    def nums(val) -> tuple:
        if not val:
            return (0.0,)
        token = val[-1] if isinstance(val, tuple) else val
        found = [float(x.replace(",", ".")) for x in re.findall(r"\d+(?:[.,]\d+)?", token)]
        return tuple(found) or (0.0,)

    def key(p: Product):
        _, attrs = _split_core_and_attrs(p.name)
        return (nums(attrs.get("sizes")), nums(attrs.get("vol")), nums(attrs.get("grit")), p.sku)

    return sorted(products, key=key)


PACK_RE = re.compile(r"\s+\d+\s*/\s*\d+\s*$")
PACK_COUNT_RE = re.compile(r"\d+\s*шт\.?(?:\s*[-–./]\s*\d+\s*уп\.?)?", re.I)
BOX_RE = re.compile(r"(?:^|[\s,;.])кор\.?(?=$|[\s,;.])", re.I)
SIZE_RE = re.compile(
    r"("
    r"\d+(?:[.,]\d+)?(?:×\d+(?:[.,]\d+)?){1,3}(?:\s*мм)?"
    r"|"
    r"\d+(?:[.,]\d+)?(?:\s*[-–]\s*\d+(?:[.,]\d+)?)?\s*мм"
    r")",
    re.I,
)
VOL_RE = re.compile(
    r"(\d+(?:[.,]\d{1,2})?\s*(?:мл|л|гр\.?|г))(?!\w)",
    re.I,
)
THREAD_RE = re.compile(r"\b[МM]\s*14\b")
GRIT_RE = re.compile(
    r"(?:(?:\b[PРpр]\s*)|(?:\bзерно\s+))(\d+(?:\s*/\s*\d+)?)\b",
    re.I,
)
ROWS_RE = re.compile(r"(\d+\s*ряд(?:а|ов)?)(?:\s+проволоки)?", re.I)
BORE_RE = re.compile(r"^(?:22[,.](?:2[23]?|3)|25[,.]4)\s*мм$", re.I)

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
    s = re.sub(r"мм\s*[xх*]", "×", s, flags=re.I)
    prev = None
    while prev != s:
        prev = s
        s = re.sub(r"(\d)\s*[xх*]\s*(\d)", r"\1×\2", s)
    return s


def _norm_measure(z: str) -> str:
    z = re.sub(r"\s*[xх*]\s*", "×", z.strip())
    z = re.sub(r"\s+", "", z)
    z = re.sub(r"мм$", " мм", z, flags=re.I)
    z = re.sub(r"мл$", " мл", z, flags=re.I)
    z = re.sub(r"гр\.?$", " гр.", z, flags=re.I)
    z = re.sub(r"(?<![м])л$", " л", z, flags=re.I)
    z = re.sub(r"(?<![р])г$", " г", z, flags=re.I)
    if "×" in z and not re.search(r"мм$", z, flags=re.I):
        z = z + " мм"
    return z.strip()


def _split_core_and_attrs(name: str) -> tuple[str, dict]:
    s = PACK_RE.sub("", _normalize_name(name)).strip()
    attrs: dict = {}
    if THREAD_RE.search(s):
        attrs["thread"] = "М14"
        s = THREAD_RE.sub(" ", s)
    sizes = SIZE_RE.findall(s)
    if sizes:
        attrs["sizes"] = tuple(_norm_measure(z) for z in sizes)
        s = SIZE_RE.sub(" ", s)
    vols = VOL_RE.findall(s)
    if vols:
        attrs["vol"] = tuple(_norm_measure(z) for z in vols)
        s = VOL_RE.sub(" ", s)
    grit = GRIT_RE.findall(s)
    if grit:
        attrs["grit"] = tuple("P" + re.sub(r"\s+", "", g) for g in grit)
        s = GRIT_RE.sub(" ", s)
    rows = ROWS_RE.findall(s)
    if rows:
        attrs["rows"] = tuple(re.sub(r"\s+", " ", r.strip()) for r in rows)
        s = ROWS_RE.sub(" ", s)
    for hard in ("жесткая", "мягкая"):
        if re.search(hard, s, re.I):
            attrs["hardness"] = hard
            s = re.sub(hard, " ", s, flags=re.I)
    s = PACK_COUNT_RE.sub(" ", s)
    s = re.sub(r"/\s*уп\.?", " ", s, flags=re.I)
    s = re.sub(r"картонный\s+подвес", " ", s, flags=re.I)
    s = BOX_RE.sub(" ", s)
    s = re.sub(r"[\(\);,]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" ,;.")
    return s, attrs


def _tidy_title(title: str) -> str:
    title = re.sub(r"\s+\.$", "", title)
    title = re.sub(r"\s+,", ",", title)
    title = re.sub(r",\s*,", ",", title)
    title = re.sub(r"\s+", " ", title)
    title = title.strip(" ,;.×")
    title = re.sub(r"\s+(по|с|и|для|из)$", "", title, flags=re.I)
    if title and title[0].islower():
        title = title[0].upper() + title[1:]
    return title.strip(" ,;.")


def _common_title(products: list[Product]) -> str:
    cores = [_split_core_and_attrs(p.name)[0] for p in products]
    if len(products) == 1:
        return _tidy_title(cores[0])
    word_lists = [c.split() for c in cores]
    prefix: list[str] = []
    for toks in zip(*word_lists):
        if len({t.lower() for t in toks}) == 1:
            prefix.append(toks[0])
        else:
            break
    title = " ".join(prefix).strip(" ,;")
    prefix_keys = {t.lower() for t in prefix}
    shared_rest: list[str] = []
    for w in word_lists[0]:
        key = w.lower()
        if key in prefix_keys:
            continue
        if all(key in {t.lower() for t in wl} for wl in word_lists):
            shared_rest.append(w)
            prefix_keys.add(key)
    if shared_rest:
        title = " ".join(prefix + shared_rest).strip(" ,;")
    if len(title) < 8:
        title = min(cores, key=len)
    hards = {a.get("hardness") for a in (_split_core_and_attrs(p.name)[1] for p in products)}
    if len(hards) == 1:
        hard = next(iter(hards))
        if hard:
            title = f"{title} {hard}"
    return _tidy_title(title)


def _strip_bore_in_size(size: str) -> str:
    """Drop 22,2 / 22,23 / 25,4 мм landing-hole parts from a compound size."""
    raw = _norm_measure(size)
    if BORE_RE.match(raw):
        return raw
    body = re.sub(r"\s*мм$", "", raw, flags=re.I)
    kept = []
    for part in body.split("×"):
        token = part.replace(" ", "")
        if re.match(r"^(?:22[,.](?:2[23]?|3)|25[,.]4)$", token):
            continue
        kept.append(part)
    if not kept:
        return raw
    out = "×".join(kept)
    if re.search(r"мм$", raw, flags=re.I) or "×" in out:
        if not re.search(r"мм$", out, flags=re.I):
            out += " мм"
    return out


def _type_sizes(sizes: tuple[str, ...] | None) -> list[str]:
    """Keep the product size; drop bore 22,23 мм and wire gauges under 2 мм."""
    if not sizes:
        return []
    kept: list[str] = []
    for s in sizes:
        if BORE_RE.match(s):
            continue
        m = re.search(r"(\d+(?:[.,]\d+)?)", s)
        num = float(m.group(1).replace(",", ".")) if m else 999
        if num < 2 and "×" not in s:
            continue
        kept.append(_strip_bore_in_size(s))
    return kept or list(sizes)


_SKIP_LEFTOVER = {
    "росомаха",
    "россомаха",
    "картонный",
    "подвес",
    "блистер",
}


def _leftover_words(core: str, title: str) -> tuple[str, ...]:
    title_words = {w.lower().strip("«»\",.") for w in title.split()}
    out = []
    for w in core.split():
        key = w.lower().strip("«»\",.")
        if key in title_words or key in _SKIP_LEFTOVER or len(w) <= 1:
            continue
        out.append(w)
    return tuple(out)


def _variant_label(p: Product, siblings: list[Product], title: str) -> str:
    mine_core, mine = _split_core_and_attrs_sku(p)
    if len(siblings) == 1:
        bits: list[str] = []
        bits.extend(_type_sizes(mine.get("sizes")))
        bits.extend(mine.get("vol") or ())
        bits.extend(mine.get("rows") or ())
        grit = mine.get("grit")
        if grit:
            bits.extend(grit if isinstance(grit, tuple) else [grit])
        if mine.get("hardness"):
            bits.append(mine["hardness"])
        if not bits and mine.get("thread"):
            bits.append(mine["thread"])
        return ", ".join(bits) if bits else "—"

    all_parsed = [_split_core_and_attrs_sku(s) for s in siblings]
    all_attrs = [a for _, a in all_parsed]
    leftovers = [_leftover_words(core, title) for core, _ in all_parsed]
    bits = []
    size_vals = [a.get("sizes") for a in all_attrs]
    if len(set(size_vals)) > 1:
        mine_sizes = mine.get("sizes") or ()
        present = [v for v in size_vals if v]
        if present and len({len(v) for v in present}) == 1:
            n = len(present[0])
            varying = [i for i in range(n) if len({v[i] for v in present}) > 1]
            bits.extend(
                _strip_bore_in_size(mine_sizes[i])
                for i in varying
                if i < len(mine_sizes)
            )
        else:
            bits.extend(_type_sizes(mine_sizes))
    for key in ("vol", "thread", "hardness", "grit", "rows"):
        vals = [a.get(key) for a in all_attrs]
        if len(set(vals)) > 1 and mine.get(key):
            val = mine[key]
            bits.extend(val if isinstance(val, tuple) else [val])
    leftover = _leftover_words(mine_core, title)
    if leftover and len(set(leftovers)) > 1:
        common = set(leftovers[0])
        for lo in leftovers[1:]:
            common &= set(lo)
        extra = [w for w in leftover if w not in common]
        if not bits:
            bits.extend(extra)
        else:
            bits.extend(w for w in extra if w.lower() in {"тонкая", "сегмент", "турбо"})
    return ", ".join(bits) if bits else "—"


# 1C omitted pack size for some SKUs; keep in sync with the matching tube/photo.
SKU_VOL_OVERRIDE = {
    "55277": "100 г",  # Смазка для редукторных передач ТМ-123
}


def _split_core_and_attrs_sku(p: Product) -> tuple[str, dict]:
    core, attrs = _split_core_and_attrs(p.name)
    extra = SKU_VOL_OVERRIDE.get(p.sku)
    if extra and not attrs.get("vol"):
        attrs = {**attrs, "vol": (extra,)}
    return core, attrs


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
        # Even template leaves «ПРОВЕРЕНО КАЧЕСТВОМ» far from the checkmark.
        tmp[0].add_redact_annot(
            pymupdf.Rect(16.0, 38.0, 68.5, 60.0),
            fill=(0.12, 0.12, 0.125),
        )
    tmp[0].apply_redactions(images=0)
    page.show_pdf_page(page.rect, tmp, 0)
    tmp.close()
    page.insert_font(fontname="segoeb", fontfile=FONT_BOLD)
    if not odd:
        font_b = pymupdf.Font(fontfile=FONT_BOLD)
        badge_x0 = 104.88
        gap = 11.34  # same as odd page, badge to caption
        right = badge_x0 - gap
        size = 7.4
        for text, baseline in (("ПРОВЕРЕНО", 45.4827), ("КАЧЕСТВОМ", 56.4827)):
            tw = font_b.text_length(text, fontsize=size)
            page.insert_text(
                (right - tw, baseline),
                text,
                fontname="segoeb",
                fontsize=size,
                color=WHITE,
            )
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


HEAD_SIZE = 5.4
TITLE_SIZE = 8.0
HEAD_INK = (0.52, 0.50, 0.48)
# Wider than this (after trim) counts as a landscape photo.
WIDE_ASPECT = 1.35


def _image_aspect(image_png: bytes | None) -> float:
    if not image_png:
        return 1.0
    with Image.open(io.BytesIO(image_png)) as im:
        w, h = im.size
    return (w / h) if h else 1.0


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


def _draw_col_headers(
    page: pymupdf.Page,
    font_r: pymupdf.Font,
    y: float,
    name_x: float,
    sku_left: float,
    price_left: float,
):
    page.insert_text(
        (name_x, y + HEAD_SIZE),
        "тип",
        fontname="segoe",
        fontsize=HEAD_SIZE,
        color=HEAD_INK,
    )
    page.insert_text(
        (sku_left, y + HEAD_SIZE),
        "код",
        fontname="segoe",
        fontsize=HEAD_SIZE,
        color=HEAD_INK,
    )
    page.insert_text(
        (price_left, y + HEAD_SIZE),
        "цена",
        fontname="segoe",
        fontsize=HEAD_SIZE,
        color=HEAD_INK,
    )


def _draw_title_band(
    page: pymupdf.Page,
    font_b: pymupdf.Font,
    rect: pymupdf.Rect,
    title: str,
) -> float:
    page.insert_font(fontname="segoeb", fontfile=FONT_BOLD)
    lines = _wrap(font_b, title, TITLE_SIZE, rect.width - 16)[:2] or [title]
    band_h = 6 + 11.2 * len(lines)
    band = pymupdf.Rect(rect.x0 + 0.8, rect.y0 + 0.8, rect.x1 - 0.8, rect.y0 + band_h)
    page.draw_rect(band, color=STRIP_FILL, fill=STRIP_FILL, width=0)
    page.draw_line(
        pymupdf.Point(band.x0 + 4, band.y1 - 0.4),
        pymupdf.Point(band.x1 - 4, band.y1 - 0.4),
        color=(0.86, 0.84, 0.82),
        width=0.4,
    )
    y = rect.y0 + 5
    for tl in lines:
        page.insert_text(
            (rect.x0 + 7.0, y + TITLE_SIZE),
            tl,
            fontname="segoeb",
            fontsize=TITLE_SIZE,
            color=INK,
        )
        y += 11.2
    return band.y1 + 3


def _table_cols(font_r: pymupdf.Font, x0: float, x1: float, products: list[Product]):
    max_sku_w = max(font_r.text_length(p.sku, fontsize=7.2) for p in products)
    max_price_w = max(font_r.text_length(p.price, fontsize=7.2) for p in products)
    sku_col_w = max(max_sku_w, font_r.text_length("код", fontsize=HEAD_SIZE))
    price_col_w = max(max_price_w, font_r.text_length("цена", fontsize=HEAD_SIZE))
    gap = 5.0
    price_r = x1
    price_left = price_r - price_col_w
    sku_r = price_left - gap
    sku_left = sku_r - sku_col_w
    name_x = x0
    name_w = max(22, sku_left - gap - name_x)
    return name_x, sku_r, price_r, name_w, sku_left, price_left


def _draw_variant_table(
    page: pymupdf.Page,
    font_r: pymupdf.Font,
    box: pymupdf.Rect,
    products: list[Product],
    labels: list[str],
    row_h: float,
    head_h: float,
    *,
    vcenter: bool,
):
    name_x, sku_r, price_r, name_w, sku_left, price_left = _table_cols(
        font_r, box.x0, box.x1, products
    )
    n = max(1, len(products))
    avail = max(1, box.height - head_h)
    vh = min(row_h, avail / n)
    block_h = head_h + n * vh
    y = box.y0
    if vcenter:
        y += max(0, (box.height - block_h) / 2)
    _draw_col_headers(page, font_r, y, name_x, sku_left, price_left)
    y += head_h
    for p, lab in zip(products, labels):
        shown = _wrap(font_r, lab, 7.2, name_w)[0]
        _draw_text_row(page, font_r, y, name_x, sku_r, price_r, shown, p.sku, p.price)
        y += vh


def draw_card(
    page: pymupdf.Page,
    placed: Placed,
    image_png: bytes | None,
    font_r: pymupdf.Font,
    font_b: pymupdf.Font,
):
    page.insert_font(fontname="segoe", fontfile=FONT_REG)
    page.insert_font(fontname="segoeb", fontfile=FONT_BOLD)
    rect = _card_rect(placed.col, placed.row, placed.slots)
    page.draw_rect(rect, color=STROKE, fill=WHITE, width=0.65, radius=0.025)

    products = list(placed.line.products)
    n = len(products)
    if n >= 2:
        products = _sort_variants(products)
    title = _common_title(products)
    labels = [_variant_label(p, products, title) for p in products]
    row_h = 13.0
    pad_bottom = 5.0
    head_h = 8.5

    below_title = _draw_title_band(page, font_b, rect, title)
    free = pymupdf.Rect(rect.x0 + 6, below_title, rect.x1 - 6, rect.y1 - pad_bottom)

    wide = _image_aspect(image_png) >= WIDE_ASPECT
    split = n <= 8 and not (n <= 2 and wide)

    if split:
        mid = free.x0 + free.width * 0.48
        img_rect = pymupdf.Rect(free.x0, free.y0, mid - 3, free.y1)
        _place_image(page, image_png, img_rect)
        table = pymupdf.Rect(mid + 2, free.y0, free.x1, free.y1)
        _draw_variant_table(
            page, font_r, table, products, labels, row_h, head_h, vcenter=True
        )
        return

    if n > 8:
        img_h = min(free.height * 0.42, 130)
        img_rect = pymupdf.Rect(free.x0 + 10, free.y0, free.x1 - 10, free.y0 + img_h)
        _place_image(page, image_png, img_rect)
        table = pymupdf.Rect(free.x0, img_rect.y1 + 3, free.x1, free.y1)
        _draw_variant_table(
            page, font_r, table, products, labels, row_h, head_h, vcenter=False
        )
        return

    table_h = head_h + n * row_h
    img_rect = pymupdf.Rect(
        free.x0, free.y0, free.x1, max(free.y0 + 8, free.y1 - table_h - 3)
    )
    _place_image(page, image_png, img_rect)
    table = pymupdf.Rect(free.x0, free.y1 - table_h, free.x1, free.y1)
    _draw_variant_table(
        page, font_r, table, products, labels, row_h, head_h, vcenter=False
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
    font_b = pymupdf.Font(fontfile=FONT_BOLD)
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
            draw_card(page, pl, img_cache[key], font_r, font_b)

    out.save(out_path, deflate=True, garbage=4)
    out.close()
    src.close()
    price.close()
    return pages
