import argparse, csv
from pathlib import Path
from typing import Tuple, List, Dict, Any
import pandas as pd
import fitz  # PyMuPDF
import math

# ===== ここは前スクリプトのコアを最小コピー =====
def rect_overlap_area(a, b):
    ax0, ay0, ax1, ay1 = a; bx0, by0, bx1, by1 = b
    x0=max(ax0,bx0); y0=max(ay0,by0); x1=min(ax1,bx1); y1=min(ay1,by1)
    return max(0.0,x1-x0)*max(0.0,y1-y0)

def rgb01_to_rgb255(c): return tuple(int(round(max(0,min(1,v))*255)) for v in c)
def rgb_dist(c1, c2): return sum((a-b)**2 for a,b in zip(c1,c2))**0.5

def group_sorted(vals, tol):
    vals=sorted(vals);
    if not vals: return []
    g=[[vals[0]]]
    for v in vals[1:]:
        if abs(v-g[-1][-1])<=tol: g[-1].append(v)
        else: g.append([v])
    return [sum(x)/len(x) for x in g]

def normalize_centers(c, target):
    c=sorted(c) or [0.0]
    while len(c)>target:
        diffs=[c[i+1]-c[i] for i in range(len(c)-1)]
        k=int(diffs.index(min(diffs))); c=c[:k]+[(c[k]+c[k+1])/2]+c[k+2:]
    while len(c)<target:
        diffs=[c[i+1]-c[i] for i in range(len(c)-1)] or [1.0]
        k=int(diffs.index(max(diffs))); c=c[:k+1]+[(c[k]+c[k+1])/2]+c[k+1:]
    return c

def centers_to_edges(centers, lo, hi):
    centers=sorted(centers)
    if len(centers)<2: return [lo, hi]
    mids=[(centers[i]+centers[i+1])/2 for i in range(len(centers)-1)]
    left = max(lo, centers[0] - (centers[1]-centers[0])/2)
    right= min(hi, centers[-1] + (centers[-1]-centers[-2])/2)
    return [left]+mids+[right]

def get_filled_rects(page, bbox):
    rects=[]
    for d in page.get_drawings():
        fill=d.get("fill")
        if not fill: continue
        color=rgb01_to_rgb255(fill)
        if float(d.get("fill_opacity",1.0))<=0.01: continue
        r=d.get("rect")
        if r and r.get_area()>0:
            rb=(r.x0,r.y0,r.x1,r.y1)
            if rect_overlap_area(rb,bbox)>0: rects.append((rb,color))
            continue
        items=d.get("items",[])
        xs,ys=[],[]
        for it in items:
            if it[0]=="re":
                r2=it[1]; rb=(r2.x0,r2.y0,r2.x1,r2.y1)
                if rect_overlap_area(rb,bbox)>0: rects.append((rb,color))
            else:
                for p in it[1:]:
                    if isinstance(p, fitz.Point): xs.append(p.x); ys.append(p.y)
        if xs and ys:
            rb=(min(xs),min(ys),max(xs),max(ys))
            if rect_overlap_area(rb,bbox)>0: rects.append((rb,color))
    return rects

def extract_one_month(doc, page_index, bbox, year, month,
                      orange_rgb=(247,199,172), rgb_tol=28.0, overlap_thresh=0.30):
    import calendar
    page=doc[page_index]
    x0,y0,x1,y1=bbox
    words=page.get_text("words")
    items=[]
    for w in words:
        wx0,wy0,wx1,wy1,txt=w[:5]
        if wx0>=x0 and wx1<=x1 and wy0>=y0 and wy1<=y1 and str(txt).isdigit():
            v=int(txt)
            if 1<=v<=31:
                items.append((v,(wx0+wx1)/2,(wy0+wy1)/2))
    if not items:
        raise RuntimeError(f"bbox {bbox} 内に日付テキストが見つかりません（月={month})")

    xs=[cx for _,cx,_ in items]; ys=[cy for *_,cy in items]
    col_centers=normalize_centers(group_sorted(xs,(x1-x0)/24.0),7)
    first_wday, num_days = calendar.monthrange(year, month)
    start_col=(first_wday+1)%7
    needed_rows=math.ceil((start_col+num_days)/7)
    row_centers=normalize_centers(group_sorted(ys,(y1-y0)/24.0),needed_rows)
    x_edges=centers_to_edges(col_centers,x0,x1)
    y_edges=centers_to_edges(row_centers,y0,y1)

    fills=get_filled_rects(page,(x0,y0,x1,y1))
    orangeish=[(rb,c) for rb,c in fills if rgb_dist(c,orange_rgb)<=rgb_tol]

    recs=[]
    d=1; r=0; c=start_col
    while d<=num_days:
        cell=(x_edges[c],y_edges[r],x_edges[c+1],y_edges[r+1])
        area=max(1.0,(cell[2]-cell[0])*(cell[3]-cell[1]))
        ov=sum(rect_overlap_area(cell,rb) for rb,_ in orangeish)
        ratio=ov/area
        recs.append({"year":year,"month":month,"date":f"{year:04d}-{month:02d}-{d:02d}",
                     "day":d,"row":r,"col":c,"is_orange":ratio>=overlap_thresh,
                     "overlap_ratio":ratio})
        d+=1; c+=1
        if c>=7: c=0; r+=1
    return pd.DataFrame.from_records(recs)
# ===== ここまでコア =====

def main():
    ap=argparse.ArgumentParser(description="FYカレンダーを月別bboxで一括抽出（ベクター塗り判定）")
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--page", type=int, default=0)
    ap.add_argument("--year", type=int, required=True, help="年度の開始年（例：2025=2025/4?2026/3）")
    ap.add_argument("--bbox-csv", required=True,
                    help="month,x0,y0,x1,y1 のCSV（例：4,45,105,215,235）を12行）")
    ap.add_argument("--rgb-tol", type=float, default=28.0)
    ap.add_argument("--overlap-thresh", type=float, default=0.30)
    ap.add_argument("--out", default="fy_orange_vector.csv")
    args=ap.parse_args()

    # CSV 読み込み（月→bbox）
    mapping=[]
    with open(args.bbox_csv, newline="", encoding="utf-8") as f:
        for row in csv.reader(f):
            if not row or row[0].strip().startswith("#"): continue
            m,intx0,inty0,intx1,inty1 = int(row[0]), float(row[1]), float(row[2]), float(row[3]), float(row[4])
            mapping.append((m,(intx0,inty0,intx1,inty1)))
    if len(mapping)!=12:
        print(f"WARNING: 月の行数が {len(mapping)} 件です。12ヶ月分の行を用意してください。")

    doc=fitz.open(args.pdf)
    dfs=[]
    # 4?12月 → 翌年1?3月
    order=list(range(4,13))+[1,2,3]
    for m,b in sorted(mapping, key=lambda x: order.index(x[0])):
        y = args.year if m>=4 else args.year+1
        df=extract_one_month(doc, args.page, b, y, m,
                             rgb_tol=args.rgb_tol, overlap_thresh=args.overlap_thresh)
        dfs.append(df)
    out=pd.concat(dfs, ignore_index=True)
    out.to_csv(args.out, index=False, encoding="utf-8-sig")
    print(f"Saved: {args.out}")
    # 参考：オレンジ日だけ一覧も表示
    print(out[out.is_orange][["date","is_orange"]].to_string(index=False))

if __name__=="__main__":
    main()