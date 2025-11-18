# mark_spots.py
import cv2
import json
import argparse
import numpy as np
import os

# Globals for mouse callback
spots = []
current_radius = 25
display_image = None
base_image = None
mouse_x = -1
mouse_y = -1

def mouse_callback(event, x, y, flags, param):
    global spots, display_image, base_image, current_radius, mouse_x, mouse_y

    if event == cv2.EVENT_MOUSEMOVE:
        mouse_x, mouse_y = x, y
        redraw()

    elif event == cv2.EVENT_LBUTTONDOWN:
        # Add a spot at the current position
        spots.append({"x": int(x), "y": int(y), "radius": int(current_radius)})
        mouse_x, mouse_y = x, y
        redraw()

def redraw():
    global display_image, base_image, spots, current_radius, mouse_x, mouse_y

    display_image = base_image.copy()

    # Draw existing spots (confirmed clicks)
    for s in spots:
        cv2.circle(display_image, (s["x"], s["y"]), s["radius"], (0, 0, 255), 2)

    # Draw preview circle under mouse cursor if valid coordinates
    if mouse_x >= 0 and mouse_y >= 0:
        cv2.circle(display_image, (mouse_x, mouse_y), current_radius, (0, 255, 255), 1)

    # Show current radius text
    cv2.putText(
        display_image,
        f"Radius: {current_radius} px",
        (10, 30),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.0,
        (0, 255, 0),
        2,
        cv2.LINE_AA,
    )

def main():
    global display_image, base_image, current_radius, spots, mouse_x, mouse_y

    parser = argparse.ArgumentParser(description="Mark dirt spots on a video frame.")
    parser.add_argument("video", help="Path to input video file")
    parser.add_argument(
        "--frame-index",
        type=int,
        default=0,
        help="Frame index to use for marking (default: 0)",
    )
    parser.add_argument(
        "--output-json",
        help="Output JSON file for spots (default: <video>.spots.json)",
    )
    args = parser.parse_args()

    video_path = args.video
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print("Error: cannot open video")
        return

    # Seek to chosen frame
    cap.set(cv2.CAP_PROP_POS_FRAMES, args.frame_index)
    ok, frame = cap.read()
    if not ok:
        print("Error: cannot read frame at index", args.frame_index)
        return

    h, w = frame.shape[:2]
    base_image = frame.copy()

    # Start with mouse preview at center of frame
    mouse_x, mouse_y = w // 2, h // 2
    redraw()

    window_name = "Mark spots (L-click=add, move=preview, +/-=radius, s=save, q=quit)"
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(window_name, mouse_callback)

    while True:
        cv2.imshow(window_name, display_image)
        key = cv2.waitKey(20) & 0xFF

        if key in (ord('q'), 27):  # q or ESC
            print("Exiting without saving.")
            spots = []
            break
        elif key == ord('s'):
            print("Saving spots...")
            break
        elif key == ord('+') or key == ord('='):
            current_radius += 2
            if current_radius > 200:
                current_radius = 200
            redraw()
        elif key == ord('-') or key == ord('_'):
            current_radius -= 2
            if current_radius < 3:
                current_radius = 3
            redraw()

    cv2.destroyAllWindows()
    cap.release()

    if not spots:
        print("No spots saved.")
        return

    output_json = args.output_json
    if not output_json:
        base, _ = os.path.splitext(video_path)
        output_json = base + ".spots.json"

    data = {
        "video_path": video_path,
        "frame_index": args.frame_index,
        "frame_width": w,
        "frame_height": h,
        "spots": spots,
    }

    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    print(f"Saved {len(spots)} spots to {output_json}")

if __name__ == "__main__":
    main()
