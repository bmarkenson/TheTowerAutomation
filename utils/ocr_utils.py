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

    Parameters:
        alpha: contrast boost factor.
        block, C: adaptive threshold params.
        close: kernel size for morphological close.
        invert: if True, returns inverted threshold (common for light-on-dark UI).
        choose_best: if True, compute both normal and inverted; pick the one with more black pixels.

    Returns:
        Single-channel uint8 binary image (0/255).
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
    OCR arbitrary text from a binary image. Returns empty string if Tesseract not available.
    """
    if not _HAS_TESS:
        return ""
    rgb = cv2.cvtColor(bin_img, cv2.COLOR_GRAY2RGB)
    cfg = f"--psm {psm}"
    return pytesseract.image_to_string(rgb, config=cfg)


def ocr_digits(bin_img: np.ndarray, *, psm: int = 7, whitelist: str = "0123456789") -> Tuple[Optional[int], float, str]:
    """
    OCR digits from a binary image.

    Returns:
        (value:int|None, avg_conf:float, raw_text:str)
        avg_conf = -1.0 when unavailable.
    """
    if not _HAS_TESS:
        return None, -1.0, ""

    rgb = cv2.cvtColor(bin_img, cv2.COLOR_GRAY2RGB)
    config = f"--psm {psm} -c tessedit_char_whitelist={whitelist}"
    data = pytesseract.image_to_data(rgb, config=config, output_type=pytesseract.Output.DICT)

    digits = []
    confs = []
    for txt, conf in zip(data.get("text", []), data.get("conf", [])):
        if txt and re.fullmatch(r"\d+", txt):
            digits.append(txt)
            try:
                c = float(conf)
                if c >= 0:
                    confs.append(c)
            except Exception:
                pass

    raw_text = "".join(digits)
    if not raw_text:
        return None, -1.0, ""
    try:
        value = int(raw_text)
    except ValueError:
        return None, -1.0, raw_text

    avg_conf = float(np.mean(confs)) if confs else -1.0
    return value, avg_conf, raw_text
