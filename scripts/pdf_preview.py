"""Render PDF pages to PNG for docs previews.

    python scripts/pdf_preview.py file.pdf out_prefix [--pages 1,2] [--scale 1.5]
"""
import argparse

import pypdfium2 as pdfium

ap = argparse.ArgumentParser()
ap.add_argument("pdf")
ap.add_argument("prefix")
ap.add_argument("--pages", default="1")
ap.add_argument("--scale", type=float, default=1.5)
a = ap.parse_args()
doc = pdfium.PdfDocument(a.pdf)
for p in [int(x) for x in a.pages.split(",")]:
    if p <= len(doc):
        doc[p - 1].render(scale=a.scale).to_pil().save(f"{a.prefix}-p{p}.png")
        print(f"{a.prefix}-p{p}.png")
