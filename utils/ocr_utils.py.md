$PROJECT_ROOT/utils/ocr_utils.py — Library
utils.ocr_utils.preprocess_binary(img_bgr, *, alpha=1.6, block=31, C=5, close=(2,2), invert=False, choose_best=False) — Returns: single-channel 0/255 image tuned for OCR (contrast boost → adaptive threshold → optional invert/best-pick → morphological close); Side effects: [cv2]; Defaults: choose_best picks normal vs inverted by black-pixel count; Errors: none material.
utils.ocr_utils.ocr_text(bin_img, *, psm=6) — Returns: OCR’d text as str ("" if Tesseract unavailable); Side effects: [cv2]; Errors: none material.
utils.ocr_utils.ocr_digits(bin_img, *, psm=7, whitelist="0123456789") — Returns: (value:int|None, avg_conf:float|-1.0, raw_text:str) using digit-only OCR; Side effects: [cv2]; Defaults: digit whitelist; Errors: none material.
