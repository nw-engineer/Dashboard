import base64, io, re, json
from typing import Dict, Any, List, Tuple
import fitz  # PyMuPDF
import cv2
import numpy as np
from PIL import Image
import pytesseract
from dateutil.parser import parse as dateparse
from collections import defaultdict

import anyio
from mcp.server.fastmcp import FastMCP

app = FastMCP(name="calendar-mcp")

# オレンジ検出のHSV範囲（必要に応じて調整）
ORANGE_RANGES = [
    ((5, 100, 120), (20, 255, 255)),   # 濃いめ
    ((20, 70, 120), (30, 255, 255)),   # 薄め?黄寄り
]
MIN_CELL_AREA = 800                    # マスの最小面積（px^2）
TESS_CONFIG_NUM  = "--psm 7 -c tessedit_char_whitelist=0123456789"
TESS_CONFIG_TEXT = "--psm 6"
RENDER_DPI = 200

def _pil_to_bgr(img: Image.Image) -> np.ndarray:
    return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)

def _pdf_to_images(pdf_bytes: bytes, dpi: int = RENDER_DPI) -> List[Tuple[int, Image.Image]]:
    out = []
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        for i, page in enumerate(doc):
            mat = fitz.Matrix(dpi/72, dpi/72)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            out.append((i, img))
    return out

def _mask_orange(bgr: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    masks = []
    for (low, high) in ORANGE_RANGES:
        masks.append(cv2.inRange(hsv, np.array(low, np.uint8), np.array(high, np.uint8)))
    m = masks[0]
    for k in masks[1:]:
        m = cv2.bitwise_or(m, k)
    kernel = np.ones((3,3), np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, kernel, iterations=1)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, kernel, iterations=1)
    return m

def _find_cells(mask: np.ndarray) -> List[Tuple[int,int,int,int,np.ndarray]]:
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cells = []
    for cnt in contours:
        x, y, w, h = cv2.boundingRect(cnt)
        if w*h < MIN_CELL_AREA: 
            continue
        ratio = w / (h + 1e-6)
        if 0.5 < ratio < 2.5:
            cells.append((x, y, w, h, cnt))
    cells.sort(key=lambda r: (r[1]//50, r[0]))  # 行優先ソート
    return cells

def _ocr_day(bgr: np.ndarray, box) -> int | None:
    x, y, w, h, _ = box
    roi = bgr[y:y+int(h*0.5), x:x+int(w*0.6)].copy()
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    gray = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY+cv2.THRESH_OTSU)[1]
    txt = pytesseract.image_to_string(gray, config=TESS_CONFIG_NUM, lang="eng")
    m = re.search(r"\b([0-3]?\d)\b", txt)
    if m:
        n = int(m.group(1))
        if 1 <= n <= 31: return n
    # 失敗→全面で再トライ
    gray2 = cv2.cvtColor(bgr[y:y+h, x:x+w], cv2.COLOR_BGR2GRAY)
    gray2 = cv2.threshold(gray2, 0, 255, cv2.THRESH_BINARY+cv2.THRESH_OTSU)[1]
    txt2 = pytesseract.image_to_string(gray2, config=TESS_CONFIG_NUM, lang="eng")
    m2 = re.search(r"\b([0-3]?\d)\b", txt2)
    if m2:
        n = int(m2.group(1))
        if 1 <= n <= 31: return n
    return None

MONTH_EN = {
    "january":1,"february":2,"march":3,"april":4,"may":5,"june":6,
    "july":7,"august":8,"september":9,"october":10,"november":11,"december":12
}

def _ocr_year_month(bgr: np.ndarray) -> Tuple[int|None, int|None]:
    h, w = bgr.shape[:2]
    header = bgr[0:int(h*0.18), 0:w]
    gray = cv2.cvtColor(header, cv2.COLOR_BGR2GRAY)
    txt = pytesseract.image_to_string(gray, config=TESS_CONFIG_TEXT, lang="jpn+eng")
    m = re.search(r"(?P<y>20\d{2})\s*年\s*(?P<m>1?\d)\s*月", txt)
    if m: return int(m.group("y")), int(m.group("m"))
    m2 = re.search(r"(?P<mon>[A-Za-z]+)\s+(?P<y>20\d{2})", txt)
    if m2:
        mon = m2.group("mon").lower()
        for k, v in MONTH_EN.items():
            if k.startswith(mon[:3]):
                return int(m2.group("y")), v
    m3 = re.search(r"(?P<y>20\d{2})\s*[/-]\s*(?P<m>1?\d)", txt)
    if m3: return int(m3.group("y")), int(m3.group("m"))
    return None, None

def _extract_holidays_from_page(pil_img: Image.Image) -> Dict[Tuple[Any,Any], List[int]]:
    bgr = _pil_to_bgr(pil_img)
    y, m = _ocr_year_month(bgr)
    mask = _mask_orange(bgr)
    cells = _find_cells(mask)
    days = set()
    for box in cells:
        dn = _ocr_day(bgr, box)
        if dn is not None:
            days.add(dn)
    key = (y if y else "unknown", m if m else "unknown")
    return {key: sorted(days)}

def _extract_holidays(pdf_bytes: bytes) -> Dict[Tuple[int|str,int|str], List[int]]:
    pages = _pdf_to_images(pdf_bytes)
    res: dict = defaultdict(set)
    for idx, pil_img in pages:
        m = _extract_holidays_from_page(pil_img)
        for k, v in m.items():
            res[k].update(v)
    return {k: sorted(list(v)) for k, v in res.items()}

def _list_holidays_in_month(data, year: int, month: int) -> List[int]:
    return data.get((year, month), [])

def _yen_range(data):
    """年末年始レンジ: 12月末の連続 & 翌1月の連続を連結"""
    years = sorted(set(y for (y,m) in data.keys() if isinstance(y,int)))
    if not years: return None
    rngs = []
    for y in years:
        dec = data.get((y,12), [])
        jan = data.get((y+1,1), [])
        tail, cur = [], []
        for d in dec:
            if d >= 25:
                if not cur or d == cur[-1]+1: cur.append(d); tail = cur[:]
                else: cur = [d]
        head, cur = [], []
        for d in jan:
            if d == 1 or (cur and d == cur[-1]+1):
                cur.append(d)
                if cur[0] == 1: head = cur[:]
            else:
                cur = [d] if d == 1 else []
        if tail or head:
            start = (y, 12, tail[0] if tail else 31)
            end   = (y+1, 1, head[-1] if head else 1)
            rngs.append((start, end))
    return rngs or None

def _bytes_from_input(pdf_path: str | None, pdf_b64: str | None) -> bytes:
    if pdf_b64:
        return base64.b64decode(pdf_b64)
    if pdf_path:
        with open(pdf_path, "rb") as f:
            return f.read()
    raise ValueError("pdf_path か pdf_base64 のどちらかを指定してください。")

@app.tool()
async def pdf_calendar_extract(
    pdf_path: str | None = None,
    pdf_base64: str | None = None,
) -> dict:
    """
    PDFのカレンダーから、オレンジ色のマス＝休みを抽出して返す。
    return: {"data": {"2025-10":[...], ...}, "raw_keys":[[2025,10], ...]}
    """
    try:
        pdf_bytes = _bytes_from_input(pdf_path, pdf_base64)
        holidays = _extract_holidays(pdf_bytes)
        # 表示用キー（"YYYY-MM"）
        nice = {}
        raw_keys = []
        for (y,m), days in holidays.items():
            if isinstance(y,int) and isinstance(m,int):
                nice[f"{y}-{m:02d}"] = days
                raw_keys.append([y,m])
        return {"data": nice, "raw_keys": raw_keys}
    except Exception as e:
        return {"error": str(e)}

@app.tool()
async def pdf_calendar_answer(
    question: str,
    pdf_path: str | None = None,
    pdf_base64: str | None = None,
    prefer_year: int | None = None,   # 複数年含む場合の優先年（任意）
) -> dict:
    """
    例: "10月の休み一覧をだして", "年末年始はいつからですか？"
    """
    try:
        pdf_bytes = _bytes_from_input(pdf_path, pdf_base64)
        data = _extract_holidays(pdf_bytes)  # key=(y,m)
        # 質問解釈（超シンプルな正規表現ベース）
        q = question.strip()

        # 「XX月の休み」
        m = re.search(r"(\d{1,2})\s*月.*休み", q)
        if m:
            mon = int(m.group(1))
            years = sorted(y for (y,_) in data.keys() if isinstance(y,int))
            y = prefer_year or (years[-1] if years else None)
            days = _list_holidays_in_month(data, y, mon) if y else []
            return {"type":"month_list", "year": y, "month": mon, "days": days}

        # 「年末年始」
        if "年末年始" in q:
            rngs = _yen_range(data)
            if not rngs:
                return {"type":"year_end_new_year", "found": False}
            start, end = rngs[-1]  # 最新
            return {"type":"year_end_new_year", "found": True, "start": start, "end": end}

        # その他: データ全体を返す
        nice = {f"{y}-{m:02d}": v for (y,m), v in data.items() if isinstance(y,int) and isinstance(m,int)}
        return {"type":"fallback", "data": nice}

    except Exception as e:
        return {"error": str(e)}


async def main():
    """
    FastMCP を SSE(=StreamableHTTP) で公開する。
    mcp のバージョン差異に備えて 2 パターン用意。
    """
    # パラメータ
    host = "0.0.0.0"
    port = 8000
    enable_docs = True  # / に簡単な動作確認ページが出る版もあります（環境による）

    # ① 新しめのAPI: app.serve_sse / serve_http がある場合
    if hasattr(app, "serve_sse"):
        await app.serve_sse(host=host, port=port, enable_docs=enable_docs)
        return
    if hasattr(app, "serve_http"):
        await app.serve_http(host=host, port=port, enable_docs=enable_docs)
        return

    # ② 旧API: create_http_app を uvicorn で立てるパターン
    try:
        http_app = app.create_http_app(enable_docs=enable_docs)  # Starlette/FastAPI app を返す実装
        import uvicorn
        uvicorn.run(http_app, host=host, port=port)
    except Exception as e:
        print("HTTP(SSE) サーバの起動APIが見つかりません。mcp パッケージのバージョンをご確認ください。")
        raise

if __name__ == "__main__":
    anyio.run(main)