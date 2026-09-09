#!/usr/bin/env python3
"""Build the next catalog pages from the TOP price list."""

from pathlib import Path

from catalog.parse_price import parse_price
from catalog.render import remaining_lines, render_new_pages

ROOT = Path(__file__).resolve().parents[1]
PRICE = ROOT / "Прайс ТОП до ЛЕЗВИЙ.pdf"
SRC = ROOT / "katalog_v12.pdf"
OUT = ROOT / "katalog_v13.pdf"


def main() -> None:
    lines = parse_price(PRICE)
    rest = remaining_lines(lines)
    pages = render_new_pages(SRC, PRICE, lines, OUT, n_new=8)
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
