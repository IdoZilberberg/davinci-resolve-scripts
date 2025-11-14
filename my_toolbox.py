# my_toolbox.py
# Place in: %AppData%\Blackmagic Design\DaVinci Resolve\Support\Fusion\Scripts\Utility\
from __future__ import annotations

import os, sys
from datetime import datetime, timedelta
from typing import Any, List, Tuple, Optional

from my_toolbox_utils import (
    format_hhmmss,
    norm_path,
    SHOT_DATETIME_FORMATS,
    SHOT_COMPACT_RE,
    SCENE_DATE_RE,
    parse_shot_datetime,
    parse_iso8601_strict,
    summarize_results,
    get_exif_data_many,
    exif_create_to_iso,
    calc_offset,
)

# ---- Resolve UI bootstrap ----
try:
    ui = fu.UIManager  # type: ignore  # noqa: F821
    dispatcher = bmd.UIDispatcher(ui)  # type: ignore
except Exception as e:
    print(f"[Clip Utils] Error accessing UI: {e}")
    print("[Clip Utils] This script must be run from DaVinci Resolve Studio.")
    sys.exit(1)

# ---------- Resolve helpers ----------
def countClipsInFolder(folder) -> int:
    total = len(folder.GetClipList() or [])
    for sub in (folder.GetSubFolderList() or []):
        total += countClipsInFolder(sub)
    return total


def applyToSelectedClips(clip_fn):
    """
    Apply a function to each selected clip.
    Returns (results_list, selected_clips_list or None)
      - selected_clips_list is None if no project is open
      - [] if project is open but no selection
    """
    pm = resolve.GetProjectManager()  # type: ignore
    proj = pm.GetCurrentProject()
    if not proj:
        print("[MediaPool] No project is open.")
        return ([], None)

    mediaPool = proj.GetMediaPool()
    selectedClips = mediaPool.GetSelectedClips() or []
    if not selectedClips:
        print("[MediaPool] No clips selected.")
        try:
            winItms["listbox"].Text = "No clips selected."
        except Exception:
            pass
        return ([], selectedClips)

    results = []
    for clip in selectedClips:
        try:
            results.append(clip_fn(clip))
        except Exception as e:
            try:
                clip_name = clip.GetName()
            except Exception:
                clip_name = "<unknown clip>"
            print(f"[Apply] Error processing {clip_name}: {e}")
            results.append({"status": "error", "clip": clip_name, "error": str(e)})
    return (results, selectedClips)


# ---------- UI-dependent helpers ----------
def get_offset_seconds() -> int:
    hours = int(winItms["hoursSpin"].Value)
    minutes = int(winItms["minutesSpin"].Value)
    seconds = int(winItms["secondsSpin"].Value)
    total_seconds = hours * 3600 + minutes * 60 + seconds
    try:
        # Checked=True means SUB (negative)
        if bool(winItms["modeToggle"].Checked):
            total_seconds = -total_seconds
    except Exception:
        pass
    return total_seconds


def updateHoursLabel():
    try:
        winItms["hoursLabel"].Text = format_hhmmss(get_offset_seconds())
    except Exception as e:
        print(f"[Update Label] Error: {e}")


def onClearListbox(_ev=None):
    winItms["listbox"].Text = ""


def onResetOffset(_ev=None):
    try:
        winItms["hoursSpin"].Value = 0
        winItms["minutesSpin"].Value = 0
        winItms["secondsSpin"].Value = 0
        updateHoursLabel()
    except Exception as e:
        print(f"[Reset] Error: {e}")


def applyISO8601ToShotAndScene(clip, iso8601_datetime: str):
    dt = parse_iso8601_strict(iso8601_datetime)
    if dt is None:
        raise ValueError(f"Invalid ISO-8601 datetime: {iso8601_datetime}")
    applyDateTimeToShotAndScene(clip, dt)


def applyDateTimeToShotAndScene(clip, date_time: datetime):
    clip.SetMetadata({
        "Shot": date_time.strftime("%Y-%m-%dT%H:%M:%S"),
        "Scene": date_time.strftime("%Y-%m-%d"),
    })


# ---------- UI actions ----------
def onCount(_ev=None):
    pm = resolve.GetProjectManager()  # type: ignore
    proj = pm.GetCurrentProject()
    if not proj:
        print("[Count Clips] No project is open.")
        return
    mediaPool = proj.GetMediaPool()
    root = mediaPool.GetRootFolder()
    total = countClipsInFolder(root)
    print(f"[Count Clips] Total clips in Media Pool: {total}")


def onPrintSelectedClips(_ev=None):
    updateHoursLabel()

    def _printer(clip):
        name = clip.GetName()
        camera = clip.GetMetadata("Camera #")
        return f"{name} ({camera})"

    results, selectedClips = applyToSelectedClips(_printer)
    if not selectedClips:
        return

    count = len(selectedClips)
    clipNames = [r for r in results if isinstance(r, str)]
    winItms["listbox"].Text = f"Selected: {count} clips\n\n" + "\n".join(clipNames)

    print(f"[Print Selected Clips] {count} clips selected")
    for name in clipNames:
        print(f"  - {name}")


def onPrintExif(_ev=None):
    """Print EXIF CreateDate for selected clips; if APPLY is enabled, write Shot/Scene."""
    def _collector(clip):
        try:
            name = clip.GetName()
        except Exception:
            name = "<unknown>"
        try:
            file_path = clip.GetClipProperty("File Path")
        except Exception:
            file_path = None
        return {"name": name, "file_path": file_path, "clip": clip}

    results, selectedClips = applyToSelectedClips(_collector)
    if not selectedClips:
        return

    clip_info = results
    file_paths = [r["file_path"] for r in clip_info if r.get("file_path")]

    if not file_paths:
        listboxText = "EXIF Data: No valid file paths found\n"
        for info in clip_info:
            listboxText += f"\n{info['name']}\n  Error: Could not get file path"
        winItms["listbox"].Text = listboxText
        return

    exif_results = get_exif_data_many(file_paths)

    if isinstance(exif_results, dict) and "error" in exif_results and len(exif_results) == 1:
        error_msg = exif_results["error"]
        winItms["listbox"].Text = f"EXIF Data: Error\n  {error_msg}"
        print(f"[Print EXIF] Error: {error_msg}")
        return

    clipLines: List[str] = []
    for info in clip_info:
        name = info["name"]
        file_path = info.get("file_path")
        if not file_path:
            clipLines.append(f"{name}\n  Error: Could not get file path")
            continue

        exif_data = None
        if isinstance(exif_results, dict):
            exif_data = exif_results.get(file_path) or exif_results.get(norm_path(file_path))

        if exif_data is None:
            clipLines.append(f"{name}\n  Error: No EXIF data found")
            continue
        if isinstance(exif_data, dict) and "error" in exif_data:
            clipLines.append(f"{name}:  Error: {exif_data['error']}")
            continue

        create_date_iso = exif_create_to_iso(exif_data.get("CreateDate", ""))
        media_create_date_iso = exif_create_to_iso(exif_data.get("MediaCreateDate", ""))
        track_create_date_iso = exif_create_to_iso(exif_data.get("TrackCreateDate", ""))

        apply_enabled = bool(winItms["dryRunToggle"].Checked)  # Checked=True => APPLY
        if apply_enabled and create_date_iso:
            try:
                applyISO8601ToShotAndScene(info["clip"], create_date_iso)
                clipLines.append(f"{name}: CreateDate: {create_date_iso}")
            except Exception as e:
                clipLines.append(f"{name} [Print EXIF] Failed to apply Shot/Scene: {e}")
        else:
            clipLines.append(f"{name}: {exif_data} Would set Shot={create_date_iso}, MediaCreateDate={media_create_date_iso} TrackCreateDate={track_create_date_iso} Scene={create_date_iso[:10]} from EXIF")

    count = len(selectedClips)
    winItms["listbox"].Text = f"EXIF Data: {count} clips\n\n" + "\n".join(clipLines)
    print(f"[Print EXIF] {count} clips selected")
    for line in clipLines:
        print(line)


def onApplyShotOffset(_ev=None):
    try:
        updateHoursLabel()
    except Exception:
        pass

    total_seconds = get_offset_seconds()

    def _apply_offset(clip):
        md = clip.GetMetadata() or {}
        shotValue = md.get("Shot")
        if not shotValue:
            return {"status": "skipped", "reason": "no Shot property", "clip": clip.GetName()}

        dt = parse_shot_datetime(shotValue)
        if dt is None:
            return {"status": "skipped", "reason": f"invalid format: {shotValue}", "clip": clip.GetName()}

        modified_dt = dt + timedelta(seconds=total_seconds)
        newValueDateTime = modified_dt.strftime("%Y-%m-%dT%H:%M:%S")

        is_dry_run = not bool(winItms["dryRunToggle"].Checked)  # Checked=True => APPLY, so DRY when unchecked
        if not is_dry_run:
            applyDateTimeToShotAndScene(clip, modified_dt)
            print(f"[Modify Shot] {clip.GetName()}: {shotValue} -> {newValueDateTime}")
        else:
            print(f"[Modify Shot] [DRY RUN] {clip.GetName()}: {shotValue} -> {newValueDateTime}")
        return {"status": "modified", "from": shotValue, "to": newValueDateTime, "clip": clip.GetName(), "dry_run": is_dry_run}

    results, selectedClips = applyToSelectedClips(_apply_offset)
    if not selectedClips:
        return

    is_dry_run = not bool(winItms["dryRunToggle"].Checked)
    dry_prefix = "[DRY RUN] " if is_dry_run else ""

    def _line_builder(r):
        if isinstance(r, dict):
            if r.get("status") == "modified":
                prefix = "[DRY RUN] " if r.get("dry_run") else ""
                return f"{prefix}Modified: {r['clip']} ({r['from']} -> {r['to']})"
            if r.get("status") == "skipped":
                return f"Skipped: {r['clip']} ({r['reason']})"
        if isinstance(r, str):
            return r
        return None

    summary = summarize_results(f"{dry_prefix}Applied {format_hhmmss(total_seconds)} offset:", results, _line_builder)
    modified = sum(1 for r in results if isinstance(r, dict) and r.get("status") == "modified")
    skipped = sum(1 for r in results if isinstance(r, dict) and r.get("status") == "skipped")
    winItms["listbox"].Text = summary
    print(f"[Modify Shot] Complete: {modified} modified, {skipped} skipped")


def onApplyShotFromFilename(_ev=None):
    def _extract_from_filename(clip):
        try:
            clip_name = clip.GetName()
        except Exception:
            clip_name = "<unknown>"
        
        md = clip.GetMetadata() or {}
        old_shot = md.get("Shot")
        old_scene = md.get("Scene")

        m_shot = SHOT_COMPACT_RE.match(clip_name) if clip_name else None
        if not m_shot:
            return {"status": "skipped", "clip": clip_name, "reason": "filename does not match compact shot pattern"}

        date_str = m_shot.group("date")   # YYYYMMDD
        time_str = m_shot.group("time")   # HHMMSS
        try:
            dt = datetime.strptime(date_str + time_str, "%Y%m%d%H%M%S")
        except Exception as e:
            return {"status": "skipped", "clip": clip_name, "reason": f"parse error: {e}"}

        # normalized strings
        new_shot = dt.strftime("%Y-%m-%dT%H:%M:%S")
        new_scene = dt.strftime("%Y-%m-%d")

        is_dry_run = not bool(winItms["dryRunToggle"].Checked)  # Checked=True => APPLY, so DRY when unchecked
        if not is_dry_run:
            applyDateTimeToShotAndScene(clip, dt)
            print(f"[Shot from Filename] {clip_name}: Shot={new_shot}, Scene={new_scene}")
        else:
            print(f"[Shot from Filename] [DRY RUN] {clip_name}: Shot={new_shot}, Scene={new_scene}")
        
        return {
            "status": "normalized",
            "clip": clip_name,
            "shot": new_shot,
            "scene": new_scene,
            "old_shot": old_shot,
            "old_scene": old_scene,
            "dry_run": is_dry_run,
        }
    
    results, selectedClips = applyToSelectedClips(_extract_from_filename)
    if not selectedClips:
        return

    is_dry_run = not bool(winItms["dryRunToggle"].Checked)
    dry_prefix = "[DRY RUN] " if is_dry_run else ""

    def _line_builder(r):
        if isinstance(r, dict):
            if r.get("status") == "normalized":
                prefix = "[DRY RUN] " if r.get("dry_run") else ""
                return f"{prefix}Extracted: {r['clip']} (Shot: {r['old_shot']} -> {r['shot']}, Scene: {r['old_scene']} -> {r['scene']})"
            if r.get("status") == "skipped":
                return f"Skipped: {r['clip']} ({r['reason']})"
        return None

    summary = summarize_results(f"{dry_prefix}Extracted Shot & Scene from filename:", results, _line_builder)
    modified = sum(1 for r in results if isinstance(r, dict) and r.get("status") == "normalized")
    skipped = sum(1 for r in results if isinstance(r, dict) and r.get("status") == "skipped")
    winItms["listbox"].Text = summary
    print(f"[Shot from Filename] Complete: {modified} extracted, {skipped} skipped")


def onApplyNormalize(_ev=None):
    """
    Normalize Shot & Scene from compact patterns to ISO-8601.
    Fixes undefined 'new_shot/new_scene' by computing them explicitly.
    """
    def _normalize(clip):
        md = clip.GetMetadata() or {}
        shot = md.get("Shot")
        scene = md.get("Scene")

        m_shot = SHOT_COMPACT_RE.match(shot or "") if shot is not None else None
        m_scene = SCENE_DATE_RE.match(scene or "") if scene is not None else None

        if not (m_shot and m_scene):
            return {"status": "skipped", "clip": clip.GetName(), "reason": "nonconforming Shot/Scene"}

        date_str = m_shot.group("date")   # YYYYMMDD
        time_str = m_shot.group("time")   # HHMMSS
        try:
            dt = datetime.strptime(date_str + time_str, "%Y%m%d%H%M%S")
        except Exception as e:
            return {"status": "skipped", "clip": clip.GetName(), "reason": f"parse error: {e}"}

        # normalized strings
        new_shot = dt.strftime("%Y-%m-%dT%H:%M:%S")
        new_scene = dt.strftime("%Y-%m-%d")

        applyDateTimeToShotAndScene(clip, dt)
        return {
            "status": "normalized",
            "clip": clip.GetName(),
            "shot": new_shot,
            "scene": new_scene,
            "old_shot": shot,
            "old_scene": scene,
        }

    results, selectedClips = applyToSelectedClips(_normalize)
    if not selectedClips:
        return

    def _line_builder(r):
        if isinstance(r, dict):
            if r.get("status") == "normalized":
                return f"Normalized: {r['clip']} (Shot: {r['old_shot']} -> {r['shot']}, Scene: {r['old_scene']} -> {r['scene']})"
            if r.get("status") == "skipped":
                return f"Skipped: {r['clip']} ({r['reason']})"
        return None

    summary = summarize_results("Normalized Shot & Scene:", results, _line_builder)
    winItms["listbox"].Text = summary


def onClose(_ev=None):
    dispatcher.ExitLoop()


def onCalcOffsetClicked(_ev=None):
    try:
        from_text = winItms["fromTimestamp"].Text
    except Exception:
        from_text = ""
    try:
        to_text = winItms["toTimestamp"].Text
    except Exception:
        to_text = ""

    result = calc_offset(from_text, to_text)
    if result is None:
        print(f"[Calc Offset] Error: invalid ISO-8601 inputs: from='{from_text}' to='{to_text}'")
        return

    hours, minutes, seconds, is_subtract = result
    try:
        winItms["hoursSpin"].Value = hours
        winItms["minutesSpin"].Value = minutes
        winItms["secondsSpin"].Value = seconds
    except Exception:
        pass

    # Set ADD/SUB mode based on the sign
    try:
        winItms["modeToggle"].Checked = is_subtract
        onToggleMode()
    except Exception:
        pass

    try:
        updateHoursLabel()
    except Exception:
        pass


# Small helper to avoid many near-identical handlers
def log_and_update(_event_name):
    def _handler(_ev=None):
        updateHoursLabel()
    return _handler


# ---------- UI ----------
win = dispatcher.AddWindow(
    {
        "ID": "MainWindow",
        "WindowTitle": "Clip Utils v0.1",
        "Geometry": [200, 200, 400, 350],
    },
    [ui.VGroup(
        {"ID": "root"},
        [
            ui.HGroup(
                [
                    ui.Button({"ID": "countBtn", "Text": "Count All Clips"}),
                    ui.Button({"ID": "printSelectedClipsBtn", "Text": "Print Selected Clips"}),
                    ui.Button({"ID": "printExif", "Text": "Print EXIF"}),
                ]
            ),
            ui.HGroup(
                [
                    ui.Button({"ID": "modeToggle", "Text": "ADD", "Checkable": True, "Checked": False}),
                    ui.Button({"ID": "resetBtn", "Text": "X", "ToolTip": "Reset", "FixedSize": [25, 25]}),
                    ui.SpinBox({"ID": "hoursSpin", "Minimum": 0, "Maximum": 999, "Value": 0}),
                    ui.Label({"Text": "H"}),
                    ui.SpinBox({"ID": "minutesSpin", "Minimum": 0, "Maximum": 59, "Value": 0}),
                    ui.Label({"Text": "M"}),
                    ui.SpinBox({"ID": "secondsSpin", "Minimum": 0, "Maximum": 59, "Value": 0}),
                    ui.Label({"Text": "S"}),
                    ui.Label({"ID": "hoursLabel", "Text": "+00:00:00"}),
                    ui.Button({"ID": "dryRunToggle", "Text": "DRY", "Checkable": True, "Checked": False}),
                    ui.Button({"ID": "applyShotOffsetBtn", "Text": "Modify Shot on selected"}),
                ]
            ),
            ui.HGroup(
                [
                    ui.Label({"Text": "From:"}),
                    ui.LineEdit({"ID": "fromTimestamp", "PlaceholderText": "YYYY-MM-DDTHH:MM:SS[.fff][Z]"}),
                    ui.Label({"Text": "To:"}),
                    ui.LineEdit({"ID": "toTimestamp", "PlaceholderText": "YYYY-MM-DDTHH:MM:SS[.fff][Z]"}),
                    ui.Button({"ID": "calcOffset", "Text": "Calc Offset"}),
                ]
            ),
            ui.HGroup(
                [
                    ui.Button({"ID": "normalizeBtn", "Text": "Normalize Shot and Scene"}),
                    ui.Button({"ID": "shotFromFilenameBtn", "Text": "Shot from filename"}),
                    ui.Button({"ID": "clearBtn", "Text": "🗑️ Clear Listbox", "ToolTip": "Clear text"}),
                ]
            ),
            # Monospace listbox without hard-coding a global stylesheet
            ui.TextEdit({
                "ID": "listbox",
                "ReadOnly": True,
                "MinimumSize": [300, 200],
                "Font": ui.Font({"Family": "Courier New", "MonoSpaced": True, "PixelSize": 14}),
            }),
        ]
    )]
)

# Connect spin events through a tiny wrapper (no globals needed)
for ev in ["ValueChanged"]:
    try:
        setattr(win.On.hoursSpin, ev, log_and_update(ev))
        setattr(win.On.minutesSpin, ev, log_and_update(ev))
        setattr(win.On.secondsSpin, ev, log_and_update(ev))
        print(f"[SpinBox] Successfully bound: {ev}")
    except Exception as e:
        print(f"[SpinBox] Failed to bind {ev}: {e}")


def onToggleMode(ev=None):
    try:
        checked = bool(winItms["modeToggle"].Checked)
        winItms["modeToggle"].Text = "SUB" if checked else "ADD"
        winItms["modeToggle"].ToolTip = (
            "Subtract from Shot & Scene. Click to Add instead" if checked
            else "Add to Shot & Scene. Click to Subtract instead"
        )
        updateHoursLabel()
    except Exception as e:
        print(f"[Toggle] Error: {e}")


def onToggleDryRun(ev=None):
    try:
        checked = bool(winItms["dryRunToggle"].Checked)
        if checked:
            winItms["dryRunToggle"].Text = "APPLY"
            winItms["dryRunToggle"].StyleSheet = "background-color: rgb(0, 100, 0); color: white;"
            winItms["dryRunToggle"].ToolTip = "Apply changes to clips. Click to simulate (DRY) instead"
        else:
            winItms["dryRunToggle"].Text = "DRY"
            winItms["dryRunToggle"].StyleSheet = "background-color: rgb(100, 0, 0); color: white;"
            winItms["dryRunToggle"].ToolTip = "Simulate changes without modifying clips. Click to Apply instead"
    except Exception as e:
        print(f"[DryRun Toggle] Error: {e}")


# Get window items and wire handlers (reading a module-level, no 'global' needed)
winItms = win.GetItems()
win.On.MainWindow.Close = onClose
win.On.countBtn.Clicked = onCount
win.On.printSelectedClipsBtn.Clicked = onPrintSelectedClips
win.On.printExif.Clicked = onPrintExif
win.On.applyShotOffsetBtn.Clicked = onApplyShotOffset
win.On.calcOffset.Clicked = onCalcOffsetClicked
win.On.modeToggle.Clicked = onToggleMode
win.On.dryRunToggle.Clicked = onToggleDryRun
win.On.resetBtn.Clicked = onResetOffset
win.On.normalizeBtn.Clicked = onApplyNormalize
win.On.shotFromFilenameBtn.Clicked = onApplyShotFromFilename
win.On.clearBtn.Clicked = onClearListbox

# Initial UI state
updateHoursLabel()
try:
    winItms["modeToggle"].ToolTip = "Add to Shot & Scene. Click to Subtract instead"
except Exception as e:
    print(f"[Init] Error setting modeToggle tooltip: {e}")

try:
    winItms["dryRunToggle"].Text = "DRY"
    winItms["dryRunToggle"].StyleSheet = "background-color: rgb(100, 0, 0); color: white;"
    winItms["dryRunToggle"].ToolTip = "Simulate changes without modifying clips. Click to Apply instead"
except Exception as e:
    print(f"[Init] Error setting DRY button: {e}")

# Show & run
win.RecalcLayout()
win.Show()
dispatcher.RunLoop()
win.Hide()
