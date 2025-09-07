# utils/ocr_utils.py
from typing import Optional, Tuple
import cv2
import numpy as np
import re

# Optional OCR backend
try:
    import pytesseract
    _HAS_TESS = True
except Exception:
    _HAS_TESS = False


def _to_rgb(img: np.ndarray) -> np.ndarray:
    """Accept 1-channel (gray/binary) or 3-channel BGR and return RGB."""
    if img is None:
        return None
    if img.ndim == 2:
        return cv2.cvtColor(img, cv2.COLOR_GRAY2RGB)
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


def preprocess_binary(
    img_bgr: np.ndarray,
    *,
    alpha: float = 1.6,
    block: int = 31,
    C: int = 5,
    close: Tuple[int, int] = (2, 2),
    invert: bool = False,
    choose_best: bool = False
) -> np.ndarray:
    """
    Convert BGR to a high-contrast binary image to favor OCR.
    """
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.convertScaleAbs(gray, alpha=alpha, beta=0)

    thr = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                                cv2.THRESH_BINARY, block, C)
    thr_inv = cv2.bitwise_not(thr)

    if choose_best:
        use_inv = (np.count_nonzero(thr_inv == 0) > np.count_nonzero(thr == 0))
        bin_img = thr_inv if use_inv else thr
    else:
        bin_img = thr_inv if invert else thr

    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, close)
    bin_img = cv2.morphologyEx(bin_img, cv2.MORPH_CLOSE, kernel, iterations=1)
    return bin_img


def ocr_text(bin_img: np.ndarray, *, psm: int = 6) -> str:
    """
    OCR arbitrary text from a binary/gray/BGR image. Returns empty string if Tesseract not available.
    """
    if not _HAS_TESS:
        return ""
    rgb = _to_rgb(bin_img)
    cfg = f"--psm {psm}"
    return pytesseract.image_to_string(rgb, config=cfg)


def ocr_text_and_conf(bin_img: np.ndarray, *, psm: int = 7) -> Tuple[str, float]:
    """
    OCR text and return (joined_text, avg_conf). avg_conf=-1.0 if unavailable.
    """
    if not _HAS_TESS:
        return "", -1.0
    rgb = _to_rgb(bin_img)
    data = pytesseract.image_to_data(rgb, config=f"--psm {psm}", output_type=pytesseract.Output.DICT)
    toks = data.get("text", [])
    confs = data.get("conf", [])
    kept, kconf = [], []
    for t, c in zip(toks, confs):
        if not t:
            continue
        kept.append(t)
        try:
            fc = float(c)
            if fc >= 0:
                kconf.append(fc)
        except Exception:
            pass
    txt = " ".join(kept).strip()
    avg_conf = float(np.mean(kconf)) if kconf else -1.0
    return txt, avg_conf


def ocr_digits(
    bin_img: np.ndarray, *,
    psm: int = 7,
    whitelist: str = "0123456789",
    combine: str = "concat"  # "concat" (legacy) or "best"
) -> Tuple[Optional[int], float, str]:
    """
    OCR digits from a binary image.

    Returns:
        (value:int|None, avg_conf:float, raw_text:str)
        avg_conf = -1.0 when unavailable.

    combine:
        - "concat": concatenate all numeric tokens (legacy behavior).
        - "best": choose the single best numeric token (prefer longer, then higher confidence).
    """
    if not _HAS_TESS:
        return None, -1.0, ""

    rgb = _to_rgb(bin_img)
    config = f"--psm {psm} -c tessedit_char_whitelist={whitelist}"
    data = pytesseract.image_to_data(rgb, config=config, output_type=pytesseract.Output.DICT)

    tokens = []
    confs = []
    for txt, conf in zip(data.get("text", []), data.get("conf", [])):
        if txt and re.fullmatch(r"\d+", txt):
            tokens.append(txt)
            try:
                c = float(conf)
                if c >= 0:
                    confs.append(c)
            except Exception:
                pass

    if not tokens:
        return None, -1.0, ""

    if combine == "best":
        # Prefer longer tokens; tie-break by confidence
        pairs = []
        # align confs length if missing
        conf_iter = iter(confs + [-1.0] * (len(tokens) - len(confs)))
        for t in tokens:
            try:
                c = next(conf_iter)
            except StopIteration:
                c = -1.0
            pairs.append((t, c))
        pairs.sort(key=lambda p: (len(p[0]), p[1]))
        best_txt, best_conf = pairs[-1]
        try:
            return int(best_txt), float(best_conf), best_txt
        except ValueError:
            return None, -1.0, best_txt

    # Legacy: concatenate all digit tokens
    raw_text = "".join(tokens)
    try:
        value = int(raw_text)
    except ValueError:
        return None, -1.0, raw_text
    avg_conf = float(np.mean(confs)) if confs else -1.0
    return value, avg_conf, raw_text


def ocr_number_with_fallback(
    bin_img: np.ndarray, *, psm_digits: int = 7, psm_text: int = 7
) -> Tuple[Optional[int], float, str]:
    """
    Robust integer OCR:
      A) Scan generic word boxes for any numeric substring (handles merged tokens like 'Wave24')
      B) If none, try digits-only OCR (single best token)
      C) If still none, run plain text OCR and regex the first integer

    Returns (value, conf, raw_text). conf=-1.0 on C) fallback.
    """
    if _HAS_TESS:
        # A) generic token scan
        rgb = _to_rgb(bin_img)
        data = pytesseract.image_to_data(rgb, config=f"--psm {psm_text}", output_type=pytesseract.Output.DICT)
        best_val, best_len, best_conf, best_raw = None, -1, -1.0, ""
        for txt, conf in zip(data.get("text", []) or [], data.get("conf", []) or []):
            if not txt:
                continue
            for m in re.finditer(r"\d{1,9}", txt):
                s = m.group(0)
                try:
                    v = int(s)
                except Exception:
                    continue
                # prefer longer numbers; tie-break on confidence
                c = -1.0
                try:
                    c = float(conf)
                except Exception:
                    pass
                if len(s) > best_len or (len(s) == best_len and c > best_conf):
                    best_val, best_len, best_conf, best_raw = v, len(s), c, txt
        if best_val is not None:
            return best_val, best_conf, best_raw

    # B) digits-only, best token
    val, conf, raw_digits = ocr_digits(bin_img, psm=psm_digits, combine="best")
    if val is not None:
        return val, conf, raw_digits

    # C) plain text OCR, regex first integer
    txt = ocr_text(bin_img, psm=psm_text) or ""
    m = re.search(r"(\d{1,9})", txt)
    if m:
        try:
            return int(m.group(1)), -1.0, txt
        except Exception:
            return None, -1.0, txt
    return None, -1.0, txt
