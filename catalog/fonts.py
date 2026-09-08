#!/usr/bin/env python3
"""Segoe UI for catalog pages — same family and metrics as katalog_v12."""

from __future__ import annotations

from pathlib import Path
from urllib.request import Request, urlopen

FONT_DIR = Path(__file__).resolve().parent / "fonts"
SEGOE_REG = FONT_DIR / "segoeui.ttf"
SEGOE_BOLD = FONT_DIR / "segoeuib.ttf"
_BASE = "https://github.com/mrbvrz/segoe-ui/raw/master/font"

# v12 FontDescriptor uses typo metrics (Ascent 728, Descent -210 per 1000).
# Unpatched Win metrics make Type0 text taller than the ReportLab subset.
_DROP_CMAP = {0x2010, 0x2011, 0xFD3E, 0xFD3F}


def _patch_segoe(path: Path) -> None:
    from fontTools.ttLib import TTFont

    font = TTFont(str(path))
    os2 = font["OS/2"]
    hhea = font["hhea"]
    cmap = font.getBestCmap()
    already = (
        hhea.ascent == os2.sTypoAscender
        and hhea.descent == os2.sTypoDescender
        and os2.usWinAscent == os2.sTypoAscender
        and os2.usWinDescent == abs(os2.sTypoDescender)
        and not (set(cmap) & _DROP_CMAP)
    )
    if already:
        return
    os2.usWinAscent = os2.sTypoAscender
    os2.usWinDescent = abs(os2.sTypoDescender)
    hhea.ascent = os2.sTypoAscender
    hhea.descent = os2.sTypoDescender
    for table in font["cmap"].tables:
        if table.isUnicode():
            for cp in list(table.cmap):
                if cp in _DROP_CMAP:
                    del table.cmap[cp]
    font.save(str(path))


def ensure_segoe_fonts() -> tuple[Path, Path]:
    FONT_DIR.mkdir(parents=True, exist_ok=True)
    for dest, name in ((SEGOE_REG, "segoeui.ttf"), (SEGOE_BOLD, "segoeuib.ttf")):
        if not dest.exists() or dest.stat().st_size < 100_000:
            req = Request(f"{_BASE}/{name}", headers={"User-Agent": "TOP-catalog"})
            with urlopen(req, timeout=30) as resp:
                dest.write_bytes(resp.read())
        _patch_segoe(dest)
    return SEGOE_REG, SEGOE_BOLD
