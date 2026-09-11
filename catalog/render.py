"""Render catalog pages in the v12 chrome: 2×6 grid, column-major, growing line blocks."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from pathlib import Path

import pymupdf
from PIL import Image

from catalog.fonts import SEGOE_BOLD, SEGOE_REG, ensure_segoe_fonts
from catalog.parse_price import Product, ProductLine, uses_two_col_table

PAGE_W, PAGE_H = 595.27557, 841.88977
FOOTER_Y = 796.54
HEADER_SRC_H = 90.71
HEADER_H = PAGE_H - FOOTER_Y  # match footer height
CONTENT_TOP = 52.0
CONTENT_BOTTOM = 790.5
COL_X = (33.9, 318.8)
CELL_W = 256.7
ROW_GAP = 5.6
SLOTS_PER_COL = 6
CELL_H = (CONTENT_BOTTOM - CONTENT_TOP - (SLOTS_PER_COL - 1) * ROW_GAP) / SLOTS_PER_COL
ROW_Y = tuple(CONTENT_TOP + i * (CELL_H + ROW_GAP) for i in range(SLOTS_PER_COL))

STROKE = (0.82, 0.79, 0.75)
LABEL = (158 / 255, 153 / 255, 148 / 255)  # #9e9994
INK = (28 / 255, 26 / 255, 23 / 255)  # #1c1a17
ORANGE = (1.0, 0.4, 0.0)
WHITE = (1, 1, 1)
RULE = (0.82, 0.80, 0.77)

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
    """Keep price order, except SDS burs: Basic, then 4-edge ROSOMAHA, then Duo."""
    tagged: list[tuple[str | None, ProductLine]] = []
    for ln in lines:
        name = ln.products[0].name if ln.products else ""
        kind = None
        if "Rennbohr Basic" in name:
            kind = "basic"
        elif "Rennbohr Duo" in name:
            kind = "duo"
        elif "4 режущие грани" in name and ln.category_code.startswith("04.03"):
            kind = "four"
        tagged.append((kind, ln))
    kinds = {k for k, _ in tagged if k}
    if not {"basic", "duo", "four"} <= kinds:
        return [ln for _, ln in tagged]
    burs = {k: ln for k, ln in tagged if k}
    out: list[ProductLine] = []
    inserted = False
    for k, ln in tagged:
        if k:
            if not inserted:
                out.extend((burs["basic"], burs["four"], burs["duo"]))
                inserted = True
            continue
        out.append(ln)
    return out


def _section(code: str) -> str:
    return code.split(".", 1)[0]


def place_pages(
    lines: list[ProductLine],
    n_pages: int | None = None,
) -> list[list[Placed]]:
    """Column-major pack of the full price list. Tall blocks that miss a leftover
    slot yield to a later same-section item."""
    pages: list[list[Placed]] = []
    used = [0, 0]
    page: list[Placed] = []
    queue = list(lines)

    def new_page():
        nonlocal used, page
        if page:
            pages.append(page)
        page = []
        used = [0, 0]

    def try_place(ln: ProductLine) -> bool:
        slots = min(ln.slots, SLOTS_PER_COL)
        if used[0] + slots <= SLOTS_PER_COL:
            page.append(Placed(ln, 0, used[0], slots))
            used[0] += slots
            return True
        if used[1] + slots <= SLOTS_PER_COL:
            page.append(Placed(ln, 1, used[1], slots))
            used[1] += slots
            return True
        return False

    def pull_filler(blocked_at: int) -> bool:
        if used[0] >= SLOTS_PER_COL and used[1] >= SLOTS_PER_COL:
            return False
        sec = _section(queue[blocked_at].category_code)
        hole = [SLOTS_PER_COL - used[0], SLOTS_PER_COL - used[1]]
        for j in range(blocked_at + 1, len(queue)):
            other = queue[j]
            if _section(other.category_code) != sec:
                return False
            if min(other.slots, SLOTS_PER_COL) <= max(hole):
                queue.insert(blocked_at, queue.pop(j))
                return True
        return False

    i = 0
    while i < len(queue):
        if n_pages is not None and len(pages) >= n_pages:
            break
        if try_place(queue[i]):
            i += 1
            if used[0] == SLOTS_PER_COL and used[1] == SLOTS_PER_COL:
                new_page()
            continue
        if pull_filler(i):
            continue
        if not page:
            break
        new_page()
        if n_pages is not None and len(pages) >= n_pages:
            break

    if page and (n_pages is None or len(pages) < n_pages):
        pages.append(page)
    return pages if n_pages is None else pages[:n_pages]


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
    def dia_key(p: Product) -> tuple:
        _, attrs = _split_core_and_attrs(p.name)
        dias = attrs.get("set_dias") or ()
        if dias:
            return (_num(dias[0]), 0.0, p.sku)
        sizes = attrs.get("sizes") or ()
        if sizes:
            parts = _size_parts_list(sizes[0])
            d = _num(parts[0]) if parts else 0.0
            ln = _num(parts[1]) if len(parts) > 1 else 0.0
            return (d, ln, p.sku)
        grit = attrs.get("grit") or ()
        if grit:
            return (_num(grit[0]), 0.0, p.sku)
        vol = attrs.get("vol") or ()
        if vol:
            return (_num(vol[0]), 0.0, p.sku)
        return (0.0, 0.0, p.sku)

    return sorted(products, key=dia_key)


def _num(token: str) -> float:
    found = re.findall(r"\d+(?:[.,]\d+)?", str(token).replace(" ", ""))
    return float(found[0].replace(",", ".")) if found else 0.0


PACK_RE = re.compile(r"\s+\d+\s*/\s*\d+\s*$")
PACK_COUNT_RE = re.compile(r"\d+\s*шт\.?(?:\s*[-–./]\s*\d+\s*уп\.?)?", re.I)
COUNT_SPEC_RE = re.compile(
    r"(\d+)\s*(предмет[аов]*|шт\.?|пр\.?(?![а-яё]))",
    re.I,
)
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
THREAD_RANGE_RE = re.compile(r"[МM]\s*(\d+)\s*[-–]\s*[МM]?\s*(\d+)")
INCH_RE = re.compile(
    r"("
    r"\d+\s+\d+\s*/\s*\d+\s*[\"″]"
    r"|"
    r"\d+\s*/\s*\d+\s*-\s*\d+\s*[A-Za-z]+"
    r"|"
    r"\d+\s*/\s*\d+\s*[\"″]"
    r"|"
    r"\d+\s*[\"″]"
    r")"
)
SDS_PAIR_RE = re.compile(
    r"(SDS\s*-?\s*MAX)\s+на\s+(SDS\s*\+?)",
    re.I,
)
CX_DIA_RE = re.compile(r"ЦХ\s+(\d+(?:[.,]\d+)?)(?!\s*мм)\b", re.I)
D_DIA_RE = re.compile(r"\bd\s*(\d+(?:[.,]\d+)?)\b", re.I)
SET_COMMA_RE = re.compile(
    r"\(?\s*((?:\d+\s*,\s*){2,}\d+)\s*мм\s*\)?",
    re.I,
)
SET_CHAIN_RE = re.compile(
    r"\(?(\d+(?:-\d+){2,})(?:\s*[xх*×]\s*(\d+))?\)?",
)
SET_SPACE_RE = re.compile(
    r"\(?\s*((?:\d+\s+){2,}\d+)\s*мм\s*\)?",
    re.I,
)
SHANK_RE = re.compile(r"хв\.?\s*\d+(?:[.,]\d+)?\s*мм", re.I)
TRI_SHANK_RE = re.compile(r"\d\s*[-–]?\s*гр\.?\s*хвост", re.I)
L_PAIR_RE = re.compile(r"\bL\s*(\d+)\s*/\s*(\d+)", re.I)
GRIT_RE = re.compile(
    r"(?:(?:\b[PРpр]\s*)|(?:\bзерно\s+))(\d+(?:\s*/\s*\d+)?)\b",
    re.I,
)
ROWS_RE = re.compile(r"(\d+\s*ряд(?:а|ов)?)(?:\s+проволоки)?", re.I)
BORE_RE = re.compile(r"^(?:22[,.](?:2[23]?|3)|25[,.]4)\s*мм$", re.I)
SAW_DIA_TEETH_RE = re.compile(r"(\d+)\s+(\d+)\s*зуб\.?", re.I)
ARBOR_RE = re.compile(r"(\d+)\s*/\s*(\d+)\s*мм", re.I)
TEETH_RE = re.compile(r"(\d+)\s*Т\b", re.I)
SAW_NAME_RE = re.compile(r"пильн", re.I)
_MAT_RE = (
    (re.compile(r"латун", re.I), "латунь"),
    (re.compile(r"нейлон", re.I), "нейлон"),
    (re.compile(r"стал", re.I), "сталь"),
)

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
    s = re.sub(r"мм\s*[xх*]\s*(?=\d)", "×", s, flags=re.I)
    s = re.sub(r"\bдер\.\s*", "дереву ", s, flags=re.I)
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
    mset = SET_COMMA_RE.search(s)
    if mset:
        attrs["set_dias"] = tuple(x.strip() for x in mset.group(1).split(",") if x.strip())
        s = SET_COMMA_RE.sub(" ", s)
    mchain = SET_CHAIN_RE.search(s)
    if mchain:
        attrs["set_dias"] = tuple(mchain.group(1).split("-"))
        if mchain.group(2):
            attrs["shared_len"] = _norm_measure(mchain.group(2) + " мм")
        s = SET_CHAIN_RE.sub(" ", s)
    if not attrs.get("set_dias"):
        mspace = SET_SPACE_RE.search(s)
        if mspace:
            attrs["set_dias"] = tuple(mspace.group(1).split())
            s = SET_SPACE_RE.sub(" ", s)
    s = SHANK_RE.sub(" ", s)
    s = TRI_SHANK_RE.sub("хвост", s)
    lp = L_PAIR_RE.search(s)
    if lp:
        attrs["L"] = f"{lp.group(1)}/{lp.group(2)}"
        s = L_PAIR_RE.sub(" ", s)
    if THREAD_RANGE_RE.search(s):
        tm = THREAD_RANGE_RE.search(s)
        attrs["thread_range"] = f"М{tm.group(1)}–М{tm.group(2)}"
        s = THREAD_RANGE_RE.sub(" ", s)
    if THREAD_RE.search(s):
        attrs["thread"] = "М14"
        s = THREAD_RE.sub(" ", s)
    inches = [re.sub(r"\s+", " ", i).strip() for i in INCH_RE.findall(s)]
    if inches:
        attrs["inch"] = tuple(inches)
        s = INCH_RE.sub(" ", s)
    sds = SDS_PAIR_RE.search(s)
    if sds:
        attrs["adapter"] = "SDS-MAX → SDS+"
        s = SDS_PAIR_RE.sub(" ", s)
    counts = []
    for n, unit in COUNT_SPEC_RE.findall(s):
        u = unit.lower()
        if u.startswith("пред"):
            counts.append(f"{n} предметов")
        elif u.startswith("пр"):
            counts.append(f"{n} пр")
        else:
            counts.append(f"{n} шт")
    if counts:
        attrs["count"] = tuple(counts)
        s = COUNT_SPEC_RE.sub(" ", s)
    saw = SAW_DIA_TEETH_RE.search(s)
    if saw:
        attrs.setdefault("sizes", ())
        attrs["sizes"] = attrs["sizes"] + (_norm_measure(saw.group(1) + " мм"),)
        attrs["teeth"] = (saw.group(2) + "Т",)
        s = SAW_DIA_TEETH_RE.sub(" ", s)
    arb = ARBOR_RE.search(s)
    if arb:
        attrs["arbor"] = (f"{arb.group(1)}/{arb.group(2)}",)
        s = ARBOR_RE.sub(" ", s)
    teeth = TEETH_RE.findall(s)
    if teeth:
        attrs["teeth"] = tuple(t + "Т" for t in teeth)
        s = TEETH_RE.sub(" ", s)
    found_sizes = SIZE_RE.findall(s)
    if found_sizes:
        sizes = []
        arbors = list(attrs.get("arbor") or ())
        for z in (_norm_measure(z) for z in found_sizes):
            if BORE_RE.match(z):
                arbors.append(re.sub(r"\s*мм$", "", z, flags=re.I).replace(".", ","))
            else:
                sizes.append(z)
        if sizes:
            attrs["sizes"] = attrs.get("sizes", ()) + tuple(sizes)
        if arbors:
            attrs["arbor"] = tuple(arbors)
        s = SIZE_RE.sub(" ", s)
    if not attrs.get("sizes"):
        cx = CX_DIA_RE.search(s)
        dmark = D_DIA_RE.search(s)
        if cx:
            attrs["sizes"] = (_norm_measure(cx.group(1) + " мм"),)
            s = CX_DIA_RE.sub(" ", s)
        elif dmark:
            attrs["sizes"] = (_norm_measure(dmark.group(1) + " мм"),)
            s = D_DIA_RE.sub(" ", s)
        elif re.search(r"коронк", s, re.I):
            tail = re.search(r"(\d{2,3})\s*$", s)
            if tail:
                attrs["sizes"] = (_norm_measure(tail.group(1) + " мм"),)
                s = s[: tail.start()].rstrip()
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
    s = re.sub(r"\s*\*\s*", " ", s)
    s = re.sub(r"[\(\);,]+", " ", s)
    s = _strip_core_noise(s, name)
    s = re.sub(r"\s+", " ", s).strip(" ,;.")
    return s, attrs


def _tidy_title(title: str) -> str:
    title = re.sub(r"\s+\.$", "", title)
    title = re.sub(r"\s+,", ",", title)
    title = re.sub(r",\s*,", ",", title)
    title = re.sub(r"\s+", " ", title)
    title = title.strip(" ,;.×")
    title = re.sub(r"\s+(по|с|и|для|из)$", "", title, flags=re.I)
    title = title.replace("камню кирпичу", "камню, кирпичу")
    title = title.replace("стеклу керамике", "стеклу, керамике")
    title = re.sub(r"/\s*(?=[А-Яа-яЁё])", " / ", title)
    title = re.sub(r'\s*"+', " ", title)
    title = re.sub(r",\s*,+", ",", title)
    title = re.sub(r"\s+", " ", title)
    if title and title[0].islower():
        title = title[0].upper() + title[1:]
    return title.strip(" ,;.")


def _word_key(token: str) -> str:
    return re.sub(r"[«»\"'.,;:]+", "", token).lower()


def _common_title(products: list[Product]) -> str:
    cores = [_split_core_and_attrs(p.name)[0] for p in products]
    if len(products) == 1:
        title = cores[0]
    else:
        word_lists = [c.split() for c in cores]
        prefix: list[str] = []
        for toks in zip(*word_lists):
            if len({_word_key(t) for t in toks}) == 1:
                prefix.append(toks[0])
            else:
                break
        title = " ".join(prefix).strip(" ,;")
        prefix_keys = {_word_key(t) for t in prefix}
        shared_rest: list[str] = []
        for w in word_lists[0]:
            key = _word_key(w)
            if not key or key in prefix_keys:
                continue
            if all(key in {_word_key(t) for t in wl} for wl in word_lists):
                shared_rest.append(w)
                prefix_keys.add(key)
        if shared_rest:
            title = " ".join(prefix + shared_rest).strip(" ,;")
        if len(title) < 8:
            title = min(cores, key=len)
        hards = {
            a.get("hardness")
            for a in (_split_core_and_attrs(p.name)[1] for p in products)
        }
        if len(hards) == 1:
            hard = next(iter(hards))
            if hard:
                title = f"{title} {hard}"
    title = _tidy_title(title)
    if products and _is_metal_drill(products[0].name):
        for q in _metal_quals(products):
            q_cmp = q.lower().replace("(tin)", "").strip()
            if q_cmp and q_cmp in title.lower():
                continue
            if q.lower() in title.lower():
                continue
            if "ступенчат" in title.lower():
                title = f"{title} {q}"
            else:
                titled, nsub = re.subn(
                    r"(Сверло по металлу)",
                    r"\1 " + q,
                    title,
                    count=1,
                    flags=re.I,
                )
                title = titled if nsub else f"{title} {q}"
    shared_len = None
    if products:
        slens = {
            _split_core_and_attrs(p.name)[1].get("shared_len") for p in products
        }
        slens.discard(None)
        if len(slens) == 1:
            shared_len = next(iter(slens))
        elif _is_spade_drill(products[0].name) or (
            _is_drill_set(products[0].name) and "перов" in products[0].name.lower()
        ):
            shared_len = _shared_compound_length(products)
        if not shared_len and (
            _is_drill_set(products[0].name) or _is_spade_drill(products[0].name)
        ):
            _, a0 = _split_core_and_attrs(products[0].name)
            for s in a0.get("sizes") or ():
                parts = _size_parts_list(s)
                if len(parts) == 1:
                    try:
                        n = float(parts[0].replace(",", "."))
                    except ValueError:
                        continue
                    if n >= 80 and all(
                        any(
                            len(_size_parts_list(z)) == 1
                            and _size_parts_list(z)[0] == parts[0]
                            for z in (
                                _split_core_and_attrs(p.name)[1].get("sizes") or ()
                            )
                        )
                        for p in products
                    ):
                        shared_len = _format_size_parts(parts)
                        break
    if shared_len and shared_len not in title:
        title = f"{title} {shared_len}"
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


def _size_parts_list(size: str) -> list[str]:
    body = re.sub(r"\s*мм$", "", _strip_bore_in_size(size), flags=re.I)
    return [p for p in body.split("×") if p]


def _format_size_parts(parts: list[str]) -> str:
    if not parts:
        return ""
    if len(parts) == 1:
        p = parts[0]
        return p if re.search(r"мм", p, re.I) else f"{p} мм"
    return "×".join(parts) + " мм"


def _differing_size_label(mine_sizes: tuple[str, ...], size_vals: list) -> list[str]:
    present = [v for v in size_vals if v]
    if not mine_sizes or not present:
        return []
    if all(len(v) == 1 for v in present):
        part_lists = [_size_parts_list(v[0]) for v in present]
        counts = {len(p) for p in part_lists}
        if len(counts) == 1 and part_lists[0]:
            nparts = len(part_lists[0])
            varying = [i for i in range(nparts) if len({pl[i] for pl in part_lists}) > 1]
            mine_parts = _size_parts_list(mine_sizes[0])
            shown = [mine_parts[i] for i in varying if i < len(mine_parts)]
            if shown:
                return [_format_size_parts(shown)]
            return _type_sizes(mine_sizes)
    if len({len(v) for v in present}) == 1:
        n = len(present[0])
        varying = [i for i in range(n) if len({v[i] for v in present}) > 1]
        return [
            _strip_bore_in_size(mine_sizes[i])
            for i in varying
            if i < len(mine_sizes)
        ]
    return _type_sizes(mine_sizes)


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
        if num < 2 and "×" not in s and not re.search(r"[-–]", s):
            continue
        kept.append(_strip_bore_in_size(s))
    return kept or list(sizes)


def _format_dia_list(dias: tuple[str, ...] | list[str]) -> str:
    return ", ".join(dias) + " мм"


def _is_drill_set(name: str) -> bool:
    n = name.lower()
    if "удлинител" in n or "установк" in n or "замок" in n or "замков" in n:
        return False
    return bool(re.search(r"набор(?:\s+ударных)?\s+сверл", n))


def _is_step_set(name: str) -> bool:
    n = name.lower()
    return "набор" in n and "ступенчат" in n


def _is_metal_drill(name: str) -> bool:
    n = name.lower()
    return bool(re.search(r"сверл[оа].*металл", n)) and "набор" not in n


def _is_spade_drill(name: str) -> bool:
    n = name.lower()
    if "набор" in n or "удлинител" in n:
        return False
    return n.lstrip().startswith("сверл") and "перов" in n


def _strip_core_noise(s: str, orig: str) -> str:
    """Drop steel codes and English series tags that belong in quals/type."""
    if not (
        _is_metal_drill(orig)
        or _is_spade_drill(orig)
        or _is_drill_set(orig)
        or _is_step_set(orig)
        or re.search(r"ступенчат", orig, re.I)
    ):
        return s
    s = re.sub(r"\bHSS(?:-[A-Za-z]+)+\b", " ", s, flags=re.I)
    s = re.sub(r"\bHSS\b", " ", s, flags=re.I)
    s = re.sub(r"\bP[56]M5(?:K5|R5)?\b", " ", s, flags=re.I)
    s = re.sub(r"\bDYNAMIC(?:-TIN)?\b", " ", s, flags=re.I)
    s = re.sub(r"\bDINAMYC-TIN\b", " ", s, flags=re.I)
    s = re.sub(r"\bDINAMIC\b", " ", s, flags=re.I)
    s = re.sub(r"\bLONG\b", " ", s, flags=re.I)
    s = re.sub(r"\bCOBALT(?:\s+CARD)?\b", " ", s, flags=re.I)
    s = re.sub(r"\bTIN\b", " ", s, flags=re.I)
    s = re.sub(r"\bCARD\b", " ", s, flags=re.I)
    s = re.sub(r"угол\s+заточки\s+\d+\s*\*?", " ", s, flags=re.I)
    s = re.sub(r",?\s*\b118\b", " ", s)
    s = re.sub(r"\bExtreme\b", " ", s, flags=re.I)
    s = re.sub(r"\b\d+\s*%", " ", s)
    s = re.sub(r"\d+\s*ступен(?:ей|и|я)?", " ", s, flags=re.I)
    if _is_metal_drill(orig):
        s = re.sub(r"\bхвост\b", " ", s, flags=re.I)
        s = re.sub(r"\bd\b", " ", s, flags=re.I)
    if _is_spade_drill(orig) or _is_drill_set(orig):
        s = re.sub(r"\bHEX\b", " ", s, flags=re.I)
        s = re.sub(r"\bхвостовик\b", " ", s, flags=re.I)
    if re.search(r"ступенчат", orig, re.I):
        s = re.sub(r"\bшаг\b", " ", s, flags=re.I)
        s = re.sub(r"\bцилиндр\.?\b", " ", s, flags=re.I)
        s = re.sub(r"\bхвостовик\b", " ", s, flags=re.I)
    if _is_drill_set(orig) or _is_step_set(orig):
        s = re.sub(r"\bцилиндр\.?\b", " ", s, flags=re.I)
        s = re.sub(r"\bхвостовик\b", " ", s, flags=re.I)
    return s


def _size_as_range(size: str) -> str | None:
    """4×10 / 4*12 → 4-10 when it is a diameter span, not dia×length."""
    parts = _size_parts_list(size)
    if len(parts) != 2:
        body = re.sub(r"\s*мм$", "", size, flags=re.I).replace("–", "-")
        if re.match(r"^\d+(?:[.,]\d+)?-\d+(?:[.,]\d+)?$", body):
            return f"{body} мм"
        return None
    try:
        a = float(parts[0].replace(",", "."))
        b = float(parts[1].replace(",", "."))
    except ValueError:
        return None
    if a < b:
        return f"{parts[0]}-{parts[1]}"
    return None


def _metal_quals(products: list[Product]) -> list[str]:
    if not products or not _is_metal_drill(products[0].name):
        return []
    n = len(products)
    need = n if n <= 2 else max(2, n - 1)
    checks = (
        (r"удлиненн|\bLONG\b|DYNAMIC(?!\s*-?\s*TIN)|DINAMIC\s+LONG", "удлиненное"),
        (r"COBALT|HSS-Co|HSS-G-Co|кобальт", "кобальтовое"),
        (r"HSS-TIN|DYNAMIC-TIN|-TIN\b|титанов", "титановое (TIN)"),
    )
    out: list[str] = []
    for pat, label in checks:
        hits = sum(1 for p in products if re.search(pat, p.name, re.I))
        if hits >= need:
            out.append(label)
    return out


def _shared_compound_length(products: list[Product]) -> str | None:
    lengths: list[str] = []
    for p in products:
        _, attrs = _split_core_and_attrs(p.name)
        sizes = attrs.get("sizes") or ()
        if not sizes:
            return None
        parts = _size_parts_list(sizes[0])
        if len(parts) < 2:
            return None
        lengths.append(parts[1])
    if lengths and len(set(lengths)) == 1:
        return _format_size_parts([lengths[0]])
    return None


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


def _is_saw(name: str) -> bool:
    return bool(SAW_NAME_RE.search(name))


def _arbor_bits(attrs: dict) -> tuple[str, ...]:
    raw = attrs.get("arbor") or ()
    out = []
    for a in raw if isinstance(raw, tuple) else (raw,):
        out.append(re.sub(r"\s*мм$", "", str(a), flags=re.I).replace(".", ",").strip())
    return tuple(out)


def _spec_bits(attrs: dict, name: str = "") -> list[str]:
    bits: list[str] = []
    if attrs.get("set_dias"):
        bits.append(_format_dia_list(attrs["set_dias"]))
        return bits
    sizes = list(attrs.get("sizes") or ())
    if _is_drill_set(name) or _is_step_set(name):
        ranges = []
        rest = []
        for s in sizes:
            rng = _size_as_range(s)
            if rng:
                parts = _size_parts_list(s)
                if len(parts) == 2:
                    b = _num(parts[1])
                    if _is_step_set(name) or b <= 16:
                        ranges.append(re.sub(r"\s*мм$", "", rng, flags=re.I))
                        continue
                elif re.search(r"\d.+\d", rng):
                    ranges.append(re.sub(r"\s*мм$", "", rng, flags=re.I))
                    continue
            rest.append(s)
        if ranges:
            bits.append(", ".join(ranges) + " мм")
            sizes = rest
        sizes = [
            s
            for s in sizes
            if not (
                len(_size_parts_list(s)) == 1 and _num(_size_parts_list(s)[0]) >= 80
            )
        ]
        bits.extend(_type_sizes(tuple(sizes) if sizes else None))
        if not bits:
            bits.extend(attrs.get("count") or ())
        return bits
    if _is_spade_drill(name) and sizes:
        parts = _size_parts_list(sizes[0])
        if parts:
            bits.append(_format_size_parts([parts[0]]))
        return bits
    if _is_metal_drill(name) and sizes:
        bits.append(_strip_bore_in_size(sizes[0]))
        if attrs.get("L") and "×" not in bits[-1]:
            bits.append(attrs["L"])
        return bits
    if _is_metal_drill(name) and attrs.get("L") and not sizes:
        if attrs.get("L"):
            bits.append(attrs["L"])
        return bits
    bits.extend(_type_sizes(tuple(sizes) if sizes else None))
    bits.extend(attrs.get("vol") or ())
    bits.extend(attrs.get("inch") or ())
    if attrs.get("thread_range"):
        bits.append(attrs["thread_range"])
    if attrs.get("adapter"):
        bits.append(attrs["adapter"])
    bits.extend(attrs.get("rows") or ())
    bits.extend(attrs.get("teeth") or ())
    grit = attrs.get("grit")
    if grit:
        bits.extend(grit if isinstance(grit, tuple) else [grit])
    if attrs.get("hardness"):
        bits.append(attrs["hardness"])
    if _is_saw(name):
        bits.extend(_arbor_bits(attrs))
    if not bits and attrs.get("thread"):
        bits.append(attrs["thread"])
    if not bits:
        bits.extend(attrs.get("count") or ())
    if attrs.get("L") and not bits:
        bits.append(attrs["L"])
    return bits


def _material_bit(name: str) -> str | None:
    for pat, label in _MAT_RE:
        if pat.search(name):
            return label
    return None


def _variant_label(p: Product, siblings: list[Product], title: str) -> str:
    mine_core, mine = _split_core_and_attrs_sku(p)
    if (
        _is_metal_drill(p.name)
        or _is_drill_set(p.name)
        or _is_step_set(p.name)
        or _is_spade_drill(p.name)
    ):
        bits = _spec_bits(mine, p.name)
        return ", ".join(bits) if bits else "—"
    if len(siblings) == 1:
        bits = _spec_bits(mine, p.name)
        if not bits:
            extra = _material_bit(p.name)
            if extra:
                bits.append(extra)
            elif re.search(r"\bDUO\b", p.name):
                bits.append("DUO")
            else:
                q = re.search(r"\bс\s+(кондуктором|фрезой)\b", p.name, re.I)
                if q:
                    bits.append(
                        {"кондуктором": "кондуктор", "фрезой": "фреза"}[q.group(1).lower()]
                    )
        return ", ".join(bits) if bits else "—"

    all_parsed = [_split_core_and_attrs_sku(s) for s in siblings]
    all_attrs = [a for _, a in all_parsed]
    leftovers = [_leftover_words(core, title) for core, _ in all_parsed]
    bits = []
    size_vals = [a.get("sizes") for a in all_attrs]
    if len(set(size_vals)) > 1:
        bits.extend(_differing_size_label(mine.get("sizes") or (), size_vals))
    for key in (
        "vol",
        "thread",
        "thread_range",
        "hardness",
        "grit",
        "rows",
        "teeth",
        "inch",
        "adapter",
    ):
        vals = [a.get(key) for a in all_attrs]
        if len(set(vals)) > 1 and mine.get(key):
            val = mine[key]
            bits.extend(val if isinstance(val, tuple) else [val])
    if _is_saw(p.name):
        bits.extend(_arbor_bits(mine))
    leftover = _leftover_words(mine_core, title)
    if leftover and len(set(leftovers)) > 1:
        common = set(leftovers[0])
        for lo in leftovers[1:]:
            common &= set(lo)
        extra = [w for w in leftover if w not in common]
        if not bits:
            bits.extend(extra)
        else:
            bits.extend(
                w
                for w in extra
                if w.lower() in {"тонкая", "сегмент", "турбо", "sds+", "шестигранный"}
            )
    if not bits:
        count_vals = [a.get("count") for a in all_attrs]
        if len(set(count_vals)) > 1 and mine.get("count"):
            bits.extend(mine["count"])
    if not bits:
        bits = _spec_bits(mine, p.name)
        bits.extend(w for w in leftover if w not in bits)
        if not bits:
            mat = _material_bit(p.name)
            if mat:
                bits.append(mat)
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


SUBTITLE = (115 / 255, 95 / 255, 99 / 255)
DARK_PANEL = (0.12, 0.12, 0.125)


def _draw_header_type(
    page: pymupdf.Page,
    odd: bool,
    left_w: float,
    right_x0: float,
    sy: float,
):
    """Title and quality captions at real metrics in the short header band."""
    page.insert_font(fontname="segoe", fontfile=FONT_REG)
    page.insert_font(fontname="segoeb", fontfile=FONT_BOLD)
    font_b = pymupdf.Font(fontfile=FONT_BOLD)
    font_r = pymupdf.Font(fontfile=FONT_REG)
    title_size = 13.5
    sub_size = 5.8
    cat = "КАТАЛОГ "
    tov = "ТОВАРОВ"
    sub = "ПРОФЕССИОНАЛЬНЫЙ ИНСТРУМЕНТ И ОСНАСТКА"
    cat_w = font_b.text_length(cat, fontsize=title_size)
    tov_w = font_b.text_length(tov, fontsize=title_size)
    sub_w = font_r.text_length(sub, fontsize=sub_size)
    title_y = 22.2
    sub_y = 32.4
    gap = 12.0
    if odd:
        x = left_w + gap
        sub_x = x
    else:
        x = right_x0 - gap - cat_w - tov_w
        sub_x = x + cat_w + tov_w - sub_w
    page.insert_text((x, title_y), cat, fontname="segoeb", fontsize=title_size, color=INK)
    page.insert_text(
        (x + cat_w, title_y), tov, fontname="segoeb", fontsize=title_size, color=ORANGE
    )
    page.insert_text(
        (sub_x, sub_y), sub, fontname="segoe", fontsize=sub_size, color=SUBTITLE
    )

    cap_size = 6.0
    if odd:
        badge_x1 = right_x0 + (481.89 - 411.0236) * sy
        tx = badge_x1 + 6.0
        for text, baseline in (("ПРОВЕРЕНО", 21.6), ("КАЧЕСТВОМ", 28.8)):
            page.insert_text(
                (tx, baseline),
                text,
                fontname="segoeb",
                fontsize=cap_size,
                color=WHITE,
            )
    else:
        badge_x0 = 104.88 * sy
        right = badge_x0 - 5.0
        for text, baseline in (("ПРОВЕРЕНО", 21.6), ("КАЧЕСТВОМ", 28.8)):
            tw = font_b.text_length(text, fontsize=cap_size)
            page.insert_text(
                (right - tw, baseline),
                text,
                fontname="segoeb",
                fontsize=cap_size,
                color=WHITE,
            )


def _stamp_chrome(page: pymupdf.Page, src: pymupdf.Document, odd: bool, number: str):
    """Short full-width header: side art keeps proportion, title is re-set."""
    tmpl = 0 if odd else 1
    tmp = pymupdf.open()
    tmp.insert_pdf(src, from_page=tmpl, to_page=tmpl)
    if odd:
        tmp[0].add_redact_annot(pymupdf.Rect(554.0, 809.5, 577.0, 829.5), fill=ORANGE)
        tmp[0].add_redact_annot(
            pymupdf.Rect(488.0, 36.0, 548.0, 62.0),
            fill=DARK_PANEL,
        )
    else:
        tmp[0].add_redact_annot(pymupdf.Rect(18.5, 809.5, 41.5, 829.5), fill=ORANGE)
        tmp[0].add_redact_annot(
            pymupdf.Rect(16.0, 38.0, 68.5, 60.0),
            fill=DARK_PANEL,
        )
    tmp[0].apply_redactions(images=0)

    sy = HEADER_H / HEADER_SRC_H
    if odd:
        left_src = pymupdf.Rect(0, 0, 147.4016, HEADER_SRC_H)
        right_src = pymupdf.Rect(411.0236, 0, PAGE_W, HEADER_SRC_H)
    else:
        left_src = pymupdf.Rect(0, 0, 184.2520, HEADER_SRC_H)
        right_src = pymupdf.Rect(447.8740, 0, PAGE_W, HEADER_SRC_H)
    left_w = left_src.width * sy
    right_w = right_src.width * sy
    right_x0 = PAGE_W - right_w

    page.draw_rect(
        pymupdf.Rect(0, 0, PAGE_W, HEADER_H),
        color=WHITE,
        fill=WHITE,
        width=0,
    )
    page.show_pdf_page(
        pymupdf.Rect(0, 0, left_w, HEADER_H),
        tmp,
        0,
        clip=left_src,
        keep_proportion=True,
    )
    page.show_pdf_page(
        pymupdf.Rect(right_x0, 0, PAGE_W, HEADER_H),
        tmp,
        0,
        clip=right_src,
        keep_proportion=True,
    )
    page.draw_rect(
        pymupdf.Rect(0, 0, PAGE_W, max(2.2, 3.685 * sy)),
        color=ORANGE,
        fill=ORANGE,
        width=0,
    )
    page.draw_line(
        pymupdf.Point(0, HEADER_H - 0.85),
        pymupdf.Point(PAGE_W, HEADER_H - 0.85),
        color=ORANGE,
        width=0.7,
    )
    page.draw_line(
        pymupdf.Point(0, HEADER_H),
        pymupdf.Point(PAGE_W, HEADER_H),
        color=DARK_PANEL,
        width=0.45,
    )
    page.show_pdf_page(
        pymupdf.Rect(0, FOOTER_Y, PAGE_W, PAGE_H),
        tmp,
        0,
        clip=pymupdf.Rect(0, FOOTER_Y, PAGE_W, PAGE_H),
    )
    tmp.close()
    page.draw_rect(
        pymupdf.Rect(0, HEADER_H - 0.15, PAGE_W, FOOTER_Y + 0.15),
        color=WHITE,
        fill=WHITE,
        width=0,
    )
    page.insert_font(fontname="segoeb", fontfile=FONT_BOLD)
    _draw_header_type(page, odd, left_w, right_x0, sy)
    x, y = _PAGE_NUM_POS[odd]
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


HEAD_SIZE = 5.0
TITLE_SIZE = 7.2
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
    name_size: float = 6.6,
):
    page.insert_text(
        (name_x, y + 7.0),
        name,
        fontname="segoe",
        fontsize=name_size,
        color=INK,
    )
    body = 6.6
    if sku:
        sku_w = font_r.text_length(sku, fontsize=body)
        page.insert_text(
            (sku_r - sku_w, y + 7.0),
            sku,
            fontname="segoe",
            fontsize=body,
            color=INK,
        )
    if price:
        price_w = font_r.text_length(price, fontsize=body)
        page.insert_text(
            (price_r - price_w, y + 7.0),
            price,
            fontname="segoe",
            fontsize=body,
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
    band_h = 4.6 + 10.0 * len(lines)
    band = pymupdf.Rect(rect.x0 + 0.8, rect.y0 + 0.8, rect.x1 - 0.8, rect.y0 + band_h)
    page.draw_rect(band, color=STRIP_FILL, fill=STRIP_FILL, width=0)
    page.draw_line(
        pymupdf.Point(band.x0 + 4, band.y1 - 0.4),
        pymupdf.Point(band.x1 - 4, band.y1 - 0.4),
        color=(0.86, 0.84, 0.82),
        width=0.4,
    )
    y = rect.y0 + 3.6
    for tl in lines:
        page.insert_text(
            (rect.x0 + 7.0, y + TITLE_SIZE),
            tl,
            fontname="segoeb",
            fontsize=TITLE_SIZE,
            color=INK,
        )
        y += 10.0
    return band.y1 + 2.0


def _table_cols(font_r: pymupdf.Font, x0: float, x1: float, products: list[Product]):
    body = 6.6
    max_sku_w = max(font_r.text_length(p.sku, fontsize=body) for p in products)
    max_price_w = max(font_r.text_length(p.price, fontsize=body) for p in products)
    sku_col_w = max(max_sku_w, font_r.text_length("код", fontsize=HEAD_SIZE))
    price_col_w = max(max_price_w, font_r.text_length("цена", fontsize=HEAD_SIZE))
    gap = 4.0
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
    columns: int | None = None,
    row_rules: bool = False,
):
    n = max(1, len(products))
    cols = 2 if (columns is None and uses_two_col_table(n)) else (columns or 1)
    if cols >= 2 and n >= 2:
        gap = 6.0
        mid_n = (n + 1) // 2
        col_w = (box.width - gap) / 2
        left = pymupdf.Rect(box.x0, box.y0, box.x0 + col_w, box.y1)
        right = pymupdf.Rect(box.x0 + col_w + gap, box.y0, box.x1, box.y1)
        _draw_variant_table(
            page,
            font_r,
            left,
            products[:mid_n],
            labels[:mid_n],
            row_h,
            head_h,
            vcenter=vcenter,
            columns=1,
            row_rules=row_rules,
        )
        _draw_variant_table(
            page,
            font_r,
            right,
            products[mid_n:],
            labels[mid_n:],
            row_h,
            head_h,
            vcenter=vcenter,
            columns=1,
            row_rules=row_rules,
        )
        x = box.x0 + col_w + gap / 2
        page.draw_line(
            pymupdf.Point(x, box.y0 + 1.0),
            pymupdf.Point(x, box.y1 - 1.0),
            color=(0.70, 0.68, 0.65),
            width=0.5,
        )
        return
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
    for i, (p, lab) in enumerate(zip(products, labels)):
        shown = lab
        if font_r.text_length(lab, fontsize=name_size) > name_w:
            name_size = max(
                4.6,
                name_size
                * name_w
                / max(1.0, font_r.text_length(lab, fontsize=name_size)),
            )
        _draw_text_row(
            page, font_r, y, name_x, sku_r, price_r, shown, p.sku, p.price, name_size
        )
        if row_rules and i + 1 < n:
            page.draw_line(
                pymupdf.Point(name_x, y + vh),
                pymupdf.Point(price_r, y + vh),
                color=RULE,
                width=0.35,
                dashes="[1.1 1.4]",
            )
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
    row_h = 11.0
    pad_bottom = 4.0
    head_h = 7.5

    below_title = _draw_title_band(page, font_b, rect, title)
    free = pymupdf.Rect(rect.x0 + 5, below_title, rect.x1 - 5, rect.y1 - pad_bottom)

    if placed.slots == 1:
        mid = free.x0 + free.width * 0.50
        img_rect = pymupdf.Rect(free.x0, free.y0, mid - 2, free.y1)
        _place_image(page, image_png, img_rect)
        table = pymupdf.Rect(mid + 2, free.y0, free.x1, free.y1)
        _draw_variant_table(
            page,
            font_r,
            table,
            products,
            labels,
            row_h,
            head_h,
            vcenter=True,
            columns=1,
        )
        return

    rows = (n + 1) // 2 if n >= 2 else n
    min_img = max(52.0, free.height * 0.30)
    table_h = min(
        head_h + rows * row_h,
        max(head_h + 9.0, free.height - min_img - 3),
    )
    img_h = max(40.0, free.height - table_h - 3)
    img_rect = pymupdf.Rect(free.x0 + 10, free.y0, free.x1 - 10, free.y0 + img_h)
    _place_image(page, image_png, img_rect)
    table = pymupdf.Rect(free.x0, img_rect.y1 + 3, free.x1, free.y1)
    _draw_variant_table(
        page,
        font_r,
        table,
        products,
        labels,
        row_h,
        head_h,
        vcenter=False,
        columns=2 if n >= 2 else 1,
        row_rules=True,
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
    n_new: int | None = None,
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
