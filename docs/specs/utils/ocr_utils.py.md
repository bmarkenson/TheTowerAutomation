
utils/ocr_utils.py
utils.ocr_utils.preprocess_binary(img_bgr, *, alpha=1.6, block=31, C=5, close=(2,2), invert=False, choose_best=False) — R: single-channel 0/255 image tuned for OCR (contrast boost → adaptive threshold → optional invert/best-pick → morphological close); S: [cv2]; Defaults: choose_best picks normal vs inverted by black-pixel count; E: none material.
utils.ocr_utils.ocr_text(bin_img, *, psm=6) — R: OCR’d text as str ("" if Tesseract unavailable); S: [cv2]; E: none material.
utils.ocr_utils.ocr_digits(bin_img, *, psm=7, whitelist="0123456789") — R: (value:int|None, avg_conf:float|-1.0, raw_text:str) using digit-only OCR; S: [cv2]; Defaults: digit whitelist; E: none material.
