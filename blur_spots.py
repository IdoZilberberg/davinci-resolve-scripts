# blur_spots.py
import cv2
import json
import argparse
import numpy as np
import os

# Default maximum allowed shift (in pixels) for spot center:
# - per frame: search radius around previous center
# - globally: total deviation from original center; if exceeded, the spot is killed
MAX_SHIFT_PIXELS = 6  # can be overridden by --max-shift

# Default minimum correlation for accepting a new match
# If matchTemplate maxVal < MATCH_THRESHOLD, we keep the previous center
MATCH_THRESHOLD = 0.5


def preprocess_for_match(gray_img: np.ndarray) -> np.ndarray:
    """
    Preprocess a grayscale image for template matching.
    Using Laplacian to emphasize edges (dirt shapes) over background changes.
    """
    lap = cv2.Laplacian(gray_img, cv2.CV_8U, ksize=3)
    return lap


def build_spot_templates(ref_frame_bgr: np.ndarray, spots, width: int, height: int):
    """
    From the reference frame and spot definitions (x, y, radius),
    build per-spot templates and metadata for tracking.

    Returns a list of dicts, one per spot:
      {
        "radius": r,
        "template": template_proc,        # processed template (Laplacian)
        "templ_w": w_t,
        "templ_h": h_t,
        "origin_cx": cx0,
        "origin_cy": cy0,
        "last_cx": cx0,
        "last_cy": cy0,
        "alive": True,
      }
    """

    gray_ref = cv2.cvtColor(ref_frame_bgr, cv2.COLOR_BGR2GRAY)
    gray_ref_proc = preprocess_for_match(gray_ref)

    spot_data = []

    for s in spots:
        cx0 = int(s["x"])
        cy0 = int(s["y"])
        r = int(s["radius"])

        # Template half-size: radius + margin
        margin = 4
        half_size = r + margin

        x1 = max(0, cx0 - half_size)
        y1 = max(0, cy0 - half_size)
        x2 = min(width, cx0 + half_size)
        y2 = min(height, cy0 + half_size)

        templ_proc = gray_ref_proc[y1:y2, x1:x2]

        if templ_proc.size == 0:
            print(f"Warning: empty template for spot at ({cx0}, {cy0}), skipping.")
            continue

        templ_h, templ_w = templ_proc.shape[:2]

        spot_data.append(
            {
                "radius": r,
                "template": templ_proc,
                "templ_w": templ_w,
                "templ_h": templ_h,
                "origin_cx": float(cx0),
                "origin_cy": float(cy0),
                "last_cx": float(cx0),
                "last_cy": float(cy0),
                "alive": True,
            }
        )

    return spot_data


def track_spot_on_frame(
    spot,
    gray_frame_proc: np.ndarray,
    width: int,
    height: int,
    max_shift: int,
    match_threshold: float,
):
    """
    Update spot["last_cx"], spot["last_cy"] by matching the template
    in a small search region around the previous center.

    max_shift is:
      - the per-frame search radius around last_cx/last_cy
      - also the global limit from origin_cx/origin_cy; if exceeded, spot is killed.
    """
    if not spot["alive"]:
        return

    templ = spot["template"]
    templ_h = spot["templ_h"]
    templ_w = spot["templ_w"]

    cx_prev = spot["last_cx"]
    cy_prev = spot["last_cy"]

    origin_cx = spot["origin_cx"]
    origin_cy = spot["origin_cy"]

    # Per-frame search region around previous center (center ± max_shift)
    half_w = templ_w // 2
    half_h = templ_h // 2

    roi_x1 = int(cx_prev - half_w - max_shift)
    roi_y1 = int(cy_prev - half_h - max_shift)
    roi_x2 = int(cx_prev + half_w + max_shift)
    roi_y2 = int(cy_prev + half_h + max_shift)

    roi_x1 = max(0, roi_x1)
    roi_y1 = max(0, roi_y1)
    roi_x2 = min(width, roi_x2)
    roi_y2 = min(height, roi_y2)

    roi = gray_frame_proc[roi_y1:roi_y2, roi_x1:roi_x2]

    # If ROI is too small to contain the template, keep previous center.
    if roi.shape[0] < templ_h or roi.shape[1] < templ_w:
        return

    res = cv2.matchTemplate(roi, templ, cv2.TM_CCOEFF_NORMED)
    min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)

    # If the match is weak, don't move the spot this frame.
    if max_val < match_threshold:
        return

    u, v = max_loc  # top-left of best match in ROI

    cx_new = roi_x1 + u + half_w
    cy_new = roi_y1 + v + half_h

    # Clamp to frame bounds
    cx_new = max(0, min(cx_new, width - 1))
    cy_new = max(0, min(cy_new, height - 1))

    # Global limit: must stay within a box of size (2*max_shift+1) centered at origin
    # dx_global = cx_new - origin_cx
    # dy_global = cy_new - origin_cy

    # if abs(dx_global) > max_shift or abs(dy_global) > max_shift:
    #     # Kill this spot: it moved too far from original center
    #     spot["alive"] = False
    #     print(
    #         f"Killing spot at origin ({origin_cx:.1f},{origin_cy:.1f}) "
    #         f"due to global shift ({dx_global:.1f},{dy_global:.1f})"
    #     )
    #     return

    # Otherwise, accept new position
    spot["last_cx"] = float(cx_new)
    spot["last_cy"] = float(cy_new)


def main():
    parser = argparse.ArgumentParser(
        description="Remove tracked spots in video using OpenCV inpainting."
    )
    parser.add_argument("video", help="Path to input video file")
    parser.add_argument("spots_json", help="JSON file with spot definitions")
    parser.add_argument(
        "--output",
        help="Output video file (default: <video>_clean.mp4)",
    )
    parser.add_argument(
        "--darken",
        type=float,
        default=1.0,
        help="Darken factor for masked (inpainted) area (0.0–1.0, default: 1.0 = no darken)",
    )
    parser.add_argument(
        "--max-shift",
        type=int,
        default=MAX_SHIFT_PIXELS,
        help=(
            "Maximum search radius in pixels around the last known center when tracking each spot "
            "in a single frame. Larger values let the tracker follow faster motion/jitter, but can "
            "increase the risk of false matches. This does NOT limit total drift over the whole clip.\n"
            f"(default: {MAX_SHIFT_PIXELS})"
        ),
    )

    parser.add_argument(
        "--match-threshold",
        type=float,
        default=MATCH_THRESHOLD,
        help=(
            "Minimum normalized correlation (0–1) from template matching required to update a spot's "
            "position in a frame. If the best match is below this value, the spot stays at its previous "
            "center for that frame. Lower values (e.g. 0.3–0.4) allow tracking through more noise but "
            "risk bad jumps; higher values (e.g. 0.6–0.8) are stricter and reduce drift but may freeze "
            "the spot when the pattern is weak.\n"
            f"(default: {MATCH_THRESHOLD})"
        ),
    )

    parser.add_argument(
        "--inpaint-radius",
        type=float,
        default=3.0,
        help="Inpainting radius (OpenCV inpaintRadius, default: 3.0)",
    )

    parser.add_argument(
        "--inpaint-method",
        choices=["telea", "ns"],
        default="telea",
        help="Inpainting method: 'telea' (fast, default) or 'ns' (Navier-Stokes).",
    )

    parser.add_argument(
        "--debug",
        action="store_true",
        help="If set, draw red circles around inpainted areas for debugging.",
    )



    args = parser.parse_args()

    video_path = args.video

    with open(args.spots_json, "r", encoding="utf-8") as f:
        data = json.load(f)

    spots = data["spots"]
    frame_width_json = int(data["frame_width"])
    frame_height_json = int(data["frame_height"])
    ref_frame_idx = int(data.get("frame_index", 0))

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print("Error: cannot open video")
        return

    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS)

    if width != frame_width_json or height != frame_height_json:
        print("Error: video resolution does not match JSON metadata")
        print("JSON:", frame_width_json, "x", frame_height_json)
        print("Video:", width, "x", height)
        cap.release()
        return

    # Read the reference frame used for spot marking
    cap.set(cv2.CAP_PROP_POS_FRAMES, ref_frame_idx)
    ok, ref_frame = cap.read()
    if not ok:
        print("Error: cannot read reference frame at index", ref_frame_idx)
        cap.release()
        return

    # Build per-spot templates from the reference frame
    spot_templates = build_spot_templates(ref_frame, spots, width, height)
    if not spot_templates:
        print("No valid spot templates built. Nothing to do.")
        cap.release()
        return

    # Rewind to the start of the video for processing
    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)

    output_path = args.output
    if not output_path:
        base, ext = os.path.splitext(video_path)
        output_path = base + "_clean.mp4"

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(output_path, fourcc, fps, (width, height))

    frame_idx = 0
    max_shift = max(1, args.max_shift)
    match_threshold = max(0.0, min(1.0, args.match_threshold))
    inpaint_radius = float(args.inpaint_radius)

    if args.inpaint_method == "telea":
        inpaint_flag = cv2.INPAINT_TELEA
    else:  # "ns"
        inpaint_flag = cv2.INPAINT_NS

    debug = args.debug
    


    while True:
        ret, frame = cap.read()
        if not ret:
            break

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray_proc = preprocess_for_match(gray)

        mask = np.zeros((height, width), dtype=np.uint8)

        active_spots = 0
        debug_circles = []  # list of (cx, cy, r) for this frame
        for spot in spot_templates:
            if not spot["alive"]:
                continue

            track_spot_on_frame(
                spot,
                gray_proc,
                width,
                height,
                max_shift,
                match_threshold,
            )
            if not spot["alive"]:
                continue

            cx = int(round(spot["last_cx"]))
            cy = int(round(spot["last_cy"]))
            r = spot["radius"]

            cv2.circle(mask, (cx, cy), r, 255, thickness=-1)
            active_spots += 1

            if debug:
                debug_circles.append((cx, cy, r))

        if active_spots > 0:
            # Inpaint the masked areas
            inpainted = cv2.inpaint(
                frame,
                mask,
                inpaintRadius=inpaint_radius,
                flags=inpaint_flag,
            )
            out = inpainted

            if args.darken < 1.0:
                mask_bool = mask == 255
                region = out[mask_bool].astype(np.float32)
                region *= args.darken
                out[mask_bool] = np.clip(region, 0, 255).astype(np.uint8)
        else:
            out = frame

        if debug and debug_circles:
            for cx, cy, r in debug_circles:
                cv2.circle(out, (cx, cy), r, (0, 0, 255), 2)    

        writer.write(out)

        frame_idx += 1

        if frame_idx % 100 == 0:
            print(
                f"Processed {frame_idx} frames... (active spots: {active_spots})",
                end="\r",
            )

    print(f"\nDone. Wrote cleaned video to {output_path}")

    cap.release()
    writer.release()


if __name__ == "__main__":
    main()
