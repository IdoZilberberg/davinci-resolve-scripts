# my_toolbox_utils.py
from __future__ import annotations

import os
import re
import json
import shutil
import subprocess
import tempfile
from datetime import datetime
from typing import List, Union, Dict, Any, Callable, Optional

# ---------- Constants / regex (pure) ----------
SHOT_DATETIME_FORMATS = [
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S.%fZ",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S.%f%z",
    "%Y-%m-%d %H:%M:%S",
]

# Shot like 20250424_01261900_FZ1000 (YYYYMMDD_HHMMSSxx_SUFFIX)
SHOT_COMPACT_WITH_CAMERA_RE = re.compile(r"^(?P<date>\d{8})_(?P<time>\d{6})(?:\d{0,2})_.+$")
# Scene like #20250424.  or  20250424
SCENE_DATE_RE = re.compile(r"^#?(?P<date>\d{8})\.?$")
FILENAME_AS_TIMESTAMP_RE = re.compile(
    r"^(?P<date>\d{8})_(?P<time>\d{6})(?:\d{0,2})?\.(?P<ext>[a-zA-Z0-9]+)$",
    re.IGNORECASE
)


# ---------- Formatting / parsing (pure) ----------
def format_hhmmss(total_seconds: int) -> str:
    sign = "+" if total_seconds >= 0 else "-"
    abs_seconds = abs(int(total_seconds))
    hh = abs_seconds // 3600
    mm = (abs_seconds % 3600) // 60
    ss = abs_seconds % 60
    return f"{sign}{hh:02d}:{mm:02d}:{ss:02d}"


def norm_path(p: str) -> str:
    """Normalize paths for consistent dictionary keys."""
    return os.path.normcase(os.path.normpath(p))


def parse_shot_datetime(shot_value: Optional[str]) -> Optional[datetime]:
    if not shot_value:
        return None
    for fmt in SHOT_DATETIME_FORMATS:
        try:
            return datetime.strptime(shot_value, fmt)
        except ValueError:
            continue
    return None


def parse_iso8601_strict(value: Optional[str]) -> Optional[datetime]:
    """Strict ISO-8601 parser for UI inputs (T-separated; optional fraction; optional Z/%z)."""
    if not value:
        return None
    formats = [
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f%z",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            pass
    return None


def summarize_results(
    title: str,
    results: List[Any],
    line_builder: Callable[[Any], Optional[str]],
) -> str:
    modified = sum(1 for r in results if isinstance(r, dict) and r.get("status") in ("modified", "normalized"))
    skipped = sum(1 for r in results if isinstance(r, dict) and r.get("status") == "skipped")
    lines: List[str] = []
    for r in results:
        try:
            line = line_builder(r)
            if line:
                lines.append(line)
        except Exception:
            pass
    summary = f"{title}\n"
    summary += f"Modified: {modified}, Skipped: {skipped}\n\n"
    summary += "\n".join(lines)
    return summary


def exif_create_to_iso(create_date: str) -> str:
    """
    Convert EXIF CreateDate 'YYYY:MM:DD HH:MM:SS' to ISO 'YYYY-MM-DDTHH:MM:SS'.
    If parse fails, returns the original string.
    """
    if not create_date or not isinstance(create_date, str):
        return create_date
    try:
        dt = datetime.strptime(create_date, "%Y:%m:%d %H:%M:%S")
        return dt.strftime("%Y-%m-%dT%H:%M:%S")
    except Exception:
        return create_date


# ---------- Batch EXIF (pure) ----------
def get_exif_data_many(file_paths: Union[str, List[str]]) -> Dict[str, Any]:
    """
    Extract EXIF data (MediaCreateDate, TrackCreateDate, CreateDate) using a SINGLE exiftool call.
    - Accepts a single path (str) or a list of paths.
    - Returns a dict keyed by normalized SourceFile (plus optional _warning/_stderr).
    """
    if not file_paths:
        return {}
    paths: List[str] = [file_paths] if isinstance(file_paths, str) else list(file_paths)

    if shutil.which("exiftool") is None:
        return {"error": "exiftool not found on PATH"}

    base_cmd = [
        "exiftool",
        "-j",
        "-CreateDate",
        "-TrackCreateDate",
        "-MediaCreateDate",
        "-charset",
        "filename=UTF8",
    ]

    cmd = list(base_cmd)
    use_filelist = sum(len(p) + 3 for p in paths) > 28000  # rough threshold

    temp_file = None
    try:
        if use_filelist:
            temp_file = tempfile.NamedTemporaryFile(mode="w", delete=False, encoding="utf-8", newline="\n")
            for p in paths:
                temp_file.write(p + "\n")
            temp_file.flush()
            temp_file.close()
            cmd += ["-@", temp_file.name]
        else:
            cmd += paths

        try:
            print(f"[EXIFTOOL] {' '.join(cmd)}")
        except Exception:
            pass

        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        stdout = proc.stdout or "[]"

        try:
            data_list = json.loads(stdout)
        except json.JSONDecodeError as e:
            return {"error": f"Failed to parse exiftool output: {e}", "stderr": proc.stderr.strip()}

        result_by_path: Dict[str, Any] = {}
        for obj in data_list:
            sf = obj.get("SourceFile")
            if sf:
                result_by_path[norm_path(sf)] = obj

        for p in paths:
            if norm_path(p) not in result_by_path:
                result_by_path[norm_path(p)] = {"error": "No data returned by exiftool"}

        if proc.returncode != 0:
            result_by_path["_warning"] = f"exiftool returned {proc.returncode}. See partial errors in stderr."
            result_by_path["_stderr"] = proc.stderr.strip()

        return result_by_path

    except subprocess.CalledProcessError as e:
        return {"error": f"exiftool error: {e.stderr.strip()}"}
    except Exception as e:
        return {"error": f"Unexpected error: {str(e)}"}
    finally:
        if temp_file is not None:
            try:
                os.unlink(temp_file.name)
            except Exception:
                pass


# ---------- Time-diff (pure) ----------
def calc_offset(from_text: Optional[str], to_text: Optional[str]) -> Optional[tuple[int, int, int, bool]]:
    """
    Calculate offset between two ISO-8601 timestamps (strict parser).
    Returns (hours, minutes, seconds, is_subtract) or None on error.
    """
    dt_from = parse_iso8601_strict(from_text)
    dt_to = parse_iso8601_strict(to_text)
    if dt_from is None or dt_to is None:
        return None

    diff_seconds = int((dt_to - dt_from).total_seconds())
    abs_seconds = abs(diff_seconds)
    hours = abs_seconds // 3600
    minutes = (abs_seconds % 3600) // 60
    seconds = abs_seconds % 60
    is_subtract = diff_seconds < 0
    return (hours, minutes, seconds, is_subtract)
