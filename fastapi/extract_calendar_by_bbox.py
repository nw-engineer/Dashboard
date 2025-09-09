import argparse
from pathlib import Path
from typing import List, Tuple

import fitz  # PyMuPDF
import numpy as np
import pandas as pd
from PIL import Image, ImageDraw

def render_guide(pdf_path: str, page_index: int = 0, scale: float = 3.0, step_pt: int = 50) -> str:
    """
    Render page with coordinate grid overlaid (PDF points shown).
    Returns path to the saved PNG.
    """
    doc = fitz.open(pdf_path)
    page = doc[page_index]
    mat = fitz.Matrix(scale, scale)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    draw = ImageDraw.Draw(img)
    W, H = img.size

    step = int(step_pt * scale)
    for x in range(0, W, step):
        draw.line([(x, 0), (x, H)], fill=(200, 200, 200), width=1)
        draw.text((x + 2, 2), f"x={x/scale:.0f}", fill=(40, 40, 40))
    for y in range(0, H, step):
        draw.line([(0, y), (W, y)], fill=(200, 200, 200), width=1)
        draw.text((2, y + 2), f"y={y/scale:.0f}", fill=(40, 40, 40))

    out_path = str(Path(pdf_path).with_suffix(f".page{page_index}_guide.png"))
    img.save(out_path)
    return out_path

def _group_sorted(vals: List[float], tol: float) -> List[float]:
    vals = sorted(vals)
    if not vals:
        return []
    groups = [[vals[0]]]
    for v in vals[1:]:
        if abs(v - groups[-1][-1]) <= tol:
            groups[-1].append(v)
        else:
            groups.append([v])
    centers = [sum(g) / len(g) for g in groups]
    return centers

def _normalize_centers(centers: List[float], target: int) -> List[float]:
    """Adjust the center list to have exactly `target` items by merging/splitting gaps."""
    c = sorted(centers)
    if not c:
        return [0.0] * target
    while len(c) > target:
        diffs = [c[i + 1] - c[i] for i in range(len(c) - 1)]
        k = int(np.argmin(diffs))
        merged = (c[k] + c[k + 1]) / 2.0
        c = c[:k] + [merged] + c[k + 2 :]
    while len(c) < target:
        diffs = [c[i + 1] - c[i] for i in range(len(c) - 1)]
        if not diffs:
            c = [c[0]] * target
            break
        k = int(np.argmax(diffs))
        insert = (c[k] + c[k + 1]) / 2.0
        c = c[: k + 1] + [insert] + c[k + 1 :]
    return c

def _nearest(val: float, centers: List[float]) -> int:
    return int(np.argmin([abs(val - c) for c in centers]))

def extract_calendar_by_bbox(
    pdf_path: str,
    page_index: int,
    bbox_pdf: Tuple[float, float, float, float],
    year: int,
    month: int,
    col_target: int = 7,
    row_target: int = 6,
    col_tol_factor: float = 0.35,
    row_tol_factor: float = 0.35,
) -> Tuple[pd.DataFrame, List[str]]:
    """
    Extract a month table within bbox_pdf:
      - Find 1..31 inside bbox
      - Group X positions (columns) and Y positions (rows) by simple proximity (no ML)
      - Normalize to 7 columns, 5-6 rows
      - Return a 7-column DataFrame and flat YYYY-MM-DD list
    """
    doc = fitz.open(pdf_path)
    page = doc[page_index]
    x0, y0, x1, y1 = bbox_pdf

    words = page.get_text("words")  # (x0,y0,x1,y1,text,...)
    items = []
    for w in words:
        wx0, wy0, wx1, wy1, txt = w[:5]
        if wx0 >= x0 and wx1 <= x1 and wy0 >= y0 and wy1 <= y1 and str(txt).isdigit():
            v = int(txt)
            if 1 <= v <= 31:
                cx = (wx0 + wx1) / 2.0
                cy = (wy0 + wy1) / 2.0
                items.append((v, cx, cy))

    if not items:
        raise RuntimeError("指定の枠内に 1..31 の数字が見つかりませんでした。")

    # Proximity thresholds based on bbox size
    col_tol = (x1 - x0) / col_target * col_tol_factor
    row_tol = (y1 - y0) / row_target * row_tol_factor

    xs = [cx for _, cx, _ in items]
    ys = [cy for _, _, cy in items]
    col_centers = _group_sorted(xs, col_tol)
    row_centers = _group_sorted(ys, row_tol)

    col_centers = _normalize_centers(col_centers, col_target)
    row_centers = _normalize_centers(row_centers, row_target)

    grid = [["" for _ in range(col_target)] for _ in range(row_target)]
    for v, cx, cy in items:
        c = _nearest(cx, col_centers)
        r = _nearest(cy, row_centers)
        grid[r][c] = v

    # Drop trailing all-empty rows
    while grid and all(cell == "" for cell in grid[-1]):
        grid.pop()

    headers = ["日", "月", "火", "水", "木", "金", "土"]
    df = pd.DataFrame(grid, columns=headers[:col_target])

    # Flat date list
    dates = []
    for r in range(len(grid)):
        for c in range(col_target):
            d = grid[r][c]
            if d != "":
                dates.append(f"{int(year):04d}-{int(month):02d}-{int(d):02d}")

    return df, dates

def parse_bbox(s: str) -> Tuple[float, float, float, float]:
    parts = [p.strip() for p in s.split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("bbox must be 'x0,y0,x1,y1' (PDF points).")
    return (float(parts[0]), float(parts[1]), float(parts[2]), float(parts[3]))

def main():
    ap = argparse.ArgumentParser(description="Extract a month table from a PDF by a given bounding box (no k-means).")
    ap.add_argument("--pdf", required=True, help="Path to PDF")
    ap.add_argument("--page", type=int, default=0, help="0-based page index")
    ap.add_argument("--year", type=int, required=True, help="Year, e.g., 2025")
    ap.add_argument("--month", type=int, required=True, help="Month, 1-12")
    # NOTE: bbox is optional now
    ap.add_argument("--bbox", type=parse_bbox, required=False, help="Bounding box in PDF points: x0,y0,x1,y1")
    ap.add_argument("--out-table", default=None, help="CSV path for the 7-column calendar table")
    ap.add_argument("--out-dates", default=None, help="TXT path for flat date list")
    ap.add_argument("--guide", action="store_true", help="Render a coordinate guide PNG for the page")
    ap.add_argument("--scale", type=float, default=3.0, help="Guide rendering scale (if --guide)")
    args = ap.parse_args()

    # GUIDE-ONLY MODE: allow running with just --guide (no --bbox)
    if args.guide and not args.bbox:
        guide_path = render_guide(args.pdf, page_index=args.page, scale=args.scale)
        print(f"Saved guide: {guide_path}")
        return

    # If no bbox provided and not guide-only, stop with a clear message
    if not args.bbox:
        raise SystemExit("Error: --bbox is required unless you only want to output the guide (use --guide).")

    # If both guide and bbox are provided, render guide too (helpful for verification)
    if args.guide and args.bbox:
        guide_path = render_guide(args.pdf, page_index=args.page, scale=args.scale)
        print(f"Saved guide: {guide_path}")

    df, dates = extract_calendar_by_bbox(
        pdf_path=args.pdf,
        page_index=args.page,
        bbox_pdf=args.bbox,
        year=args.year,
        month=args.month,
    )

    print("Table preview:")
    print(df)

    if args.out_table:
        Path(args.out_table).parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(args.out_table, index=False)
        print(f"Saved calendar table CSV: {args.out_table}")

    if args.out_dates:
        Path(args.out_dates).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out_dates).write_text("\n".join(dates), encoding="utf-8")
        print(f"Saved date list: {args.out_dates}")

if __name__ == "__main__":
    main()
