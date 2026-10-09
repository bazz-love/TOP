#!/usr/bin/env python3
"""Build catalog pages from Excel parts 1–6."""

from pathlib import Path

from catalog.parse_price import (
    apply_excel_photo_overrides,
    attach_pdf_images,
    parse_excels,
)
from catalog.render import remaining_lines, render_new_pages

ROOT = Path(__file__).resolve().parents[1]
EXCELS = [ROOT / f"{i}.xlsx" for i in range(1, 7)]
PRICE = ROOT / "Прайс ТОП до ЛЕЗВИЙ.pdf"
SRC = ROOT / "katalog_v12.pdf"
if not SRC.exists():
    SRC = ROOT / "katalog_v13.pdf"
OUT = ROOT / "katalog_v13.pdf"


def main() -> None:
    lines = parse_excels(EXCELS)
    attach_pdf_images(lines, PRICE)
    apply_excel_photo_overrides(lines)
    rest = remaining_lines(lines)
    pages = render_new_pages(SRC, PRICE, lines, OUT)
    print(f"wrote {OUT}  ({len(pages)} pages)")
    for i, page in enumerate(pages, start=1):
        print(f"  page {i:02d}")
        for pl in sorted(page, key=lambda p: (p.col, p.row)):
            skus = ", ".join(p.sku for p in pl.line.products)
            print(
                f"    col={pl.col} row={pl.row} slots={pl.slots} "
                f"{pl.line.category_code} n={len(pl.line.products)} [{skus}]"
            )
    print(f"queued after these pages: {len(rest) - sum(len(p) for p in pages)} lines")


if __name__ == "__main__":
    main()
