import cv2
import os
import sys

if len(sys.argv) != 2:
    print("Usage: python tools/crop_region.py <output_filename.png>")
    sys.exit(1)

OUTPUT_FILENAME = sys.argv[1]
SOURCE_PATH = "screenshots/latest.png"
DEST_PATH = os.path.join("assets/match_templates", OUTPUT_FILENAME)

cropping = False
start_point = None
end_point = None
image = cv2.imread(SOURCE_PATH)
clone = image.copy()

def click_and_crop(event, x, y, flags, param):
    global cropping, start_point, end_point, image

    if event == cv2.EVENT_LBUTTONDOWN:
        start_point = (x, y)
        cropping = True
        end_point = None  # reset

    elif event == cv2.EVENT_MOUSEMOVE and cropping:
        end_point = (x, y)

    elif event == cv2.EVENT_LBUTTONUP:
        end_point = (x, y)
        cropping = False

        x1, y1 = min(start_point[0], end_point[0]), min(start_point[1], end_point[1])
        x2, y2 = max(start_point[0], end_point[0]), max(start_point[1], end_point[1])
        crop = clone[y1:y2, x1:x2]

        if crop.size > 0:
            os.makedirs(os.path.dirname(DEST_PATH), exist_ok=True)
            cv2.imwrite(DEST_PATH, crop)
            print(f"[INFO] Saved to {DEST_PATH}")
            print(f"[INFO] Region: x={x1}, y={y1}, w={x2 - x1}, h={y2 - y1}")
        else:
            print("[WARN] Empty crop. Try again.")

        image[:] = clone  # reset display

cv2.namedWindow("Crop Tool")
cv2.setMouseCallback("Crop Tool", click_and_crop)

print("[INFO] Click and drag to select region. Press 'q' or ESC to quit.")

while True:
    display = image.copy()
    if cropping and start_point and end_point:
        cv2.rectangle(display, start_point, end_point, (0, 255, 0), 2)
    cv2.imshow("Crop Tool", display)
    key = cv2.waitKey(1) & 0xFF
    if key == 27 or key == ord("q"):
        break

cv2.destroyAllWindows()


