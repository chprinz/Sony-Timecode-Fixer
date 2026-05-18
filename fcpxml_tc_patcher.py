#!/usr/bin/env python3
"""
Patch Sony XAVC-S MP4 timecode starts in an FCPXML export so DaVinci Resolve can
relink clips using the real embedded camera timecode.

Usage example:
    python3 fcpxml_tc_patcher.py /path/to/export.fcpxml /path/to/sony_clips/
    python3 fcpxml_tc_patcher.py /path/to/export.fcpxml

Requirements:
    - Python 3 standard library
    - ffprobe from FFmpeg installed and available on PATH
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Dict, Iterable, Optional, Set
from urllib.parse import unquote, urlparse


SOURCE_START_ELEMENTS = {"asset-clip", "clip", "ref-clip", "video"}
FFPROBE_BINARY = "ffprobe"


@dataclass(frozen=True)
class FormatInfo:
    """Frame timing for an FCPXML format resource."""

    frame_rate: Fraction
    frame_duration: Fraction
    time_denominator: int


@dataclass(frozen=True)
class AssetPatch:
    """All data needed to patch XML nodes that refer to one asset."""

    asset_id: str
    filename: str
    original_start: Fraction
    real_start: Fraction
    offset: Fraction
    original_tc: str
    real_tc: str
    denominator: int


@dataclass(frozen=True)
class Mp4Info:
    """Resolved MP4 path and its ffprobe timecode, if available."""

    path: Path
    timecode: Optional[str]


@dataclass
class PatchStats:
    """Counts and filenames skipped while scanning XML assets."""

    assets_seen: int = 0
    matched_mp4: int = 0
    no_timecode: int = 0
    no_match: int = 0
    no_format: int = 0
    patched: int = 0
    no_timecode_files: Optional[list[str]] = None

    def __post_init__(self) -> None:
        if self.no_timecode_files is None:
            self.no_timecode_files = []


def local_name(tag: str) -> str:
    """Return an XML tag name without its namespace."""

    return tag.rsplit("}", 1)[-1] if tag.startswith("{") else tag


def iter_elements_named(root: ET.Element, name: str) -> Iterable[ET.Element]:
    """Yield every element whose local tag name matches name."""

    for element in root.iter():
        if local_name(element.tag) == name:
            yield element


def parse_fcpxml_time(value: Optional[str]) -> Fraction:
    """
    Parse an FCPXML time value such as "1001/24000s", "10s", or "-5/24s".

    FCPXML stores seconds as rational values with a trailing "s" suffix.
    Missing values are treated as 0s because FCPXML often omits default times.
    """

    if value is None:
        return Fraction(0, 1)

    text = value.strip()
    if not text.endswith("s"):
        raise ValueError(f"FCPXML time value does not end with 's': {value!r}")

    number = text[:-1]
    if "/" in number:
        numerator, denominator = number.split("/", 1)
        return Fraction(int(numerator), int(denominator))

    return Fraction(int(number), 1)


def format_fcpxml_time(value: Fraction, denominator: int) -> str:
    """
    Format a time using the target FCPXML denominator.

    The numerator is kept as an integer against the same timebase used by the
    clip/format, preserving the rational-number style FCPXML expects.
    """

    numerator = value * denominator
    if numerator.denominator != 1:
        raise ValueError(
            f"Cannot represent {value} exactly with denominator {denominator}"
        )

    numerator_int = numerator.numerator
    if numerator_int == 0:
        return "0s"
    return f"{numerator_int}/{denominator}s"


def parse_rational(value: str) -> Fraction:
    """Parse a rational number without an FCPXML seconds suffix."""

    text = value.strip()
    if "/" in text:
        numerator, denominator = text.split("/", 1)
        return Fraction(int(numerator), int(denominator))
    return Fraction(int(text), 1)


def parse_timecode_to_frames(timecode: str, frame_rate: Fraction) -> int:
    """
    Convert HH:MM:SS:FF or HH:MM:SS;FF to a frame count.

    Drop-frame timecode is detected by a semicolon. The standard NTSC drop-frame
    counting rule is applied for 29.97/59.94-style rates.
    """

    separator = ";" if ";" in timecode else ":"
    parts = timecode.strip().replace(".", separator).split(separator)
    if len(parts) != 4:
        raise ValueError(f"Unsupported timecode format: {timecode!r}")

    hours, minutes, seconds, frames = (int(part) for part in parts)
    nominal_fps = round(float(frame_rate))

    if not 0 <= minutes < 60 or not 0 <= seconds < 60 or not 0 <= frames < nominal_fps:
        raise ValueError(f"Timecode value is out of range for {frame_rate}: {timecode}")

    total_minutes = hours * 60 + minutes
    total_frames = (
        ((hours * 3600) + (minutes * 60) + seconds) * nominal_fps
        + frames
    )

    if separator == ";":
        drop_frames = round(nominal_fps * 0.066666)
        total_frames -= drop_frames * (total_minutes - total_minutes // 10)

    return total_frames


def timecode_to_fcpxml_time(timecode: str, format_info: FormatInfo) -> Fraction:
    """Convert camera timecode to an FCPXML time value in seconds."""

    total_frames = parse_timecode_to_frames(timecode, format_info.frame_rate)
    return Fraction(total_frames, 1) * format_info.frame_duration


def time_to_display_tc(value: Fraction, frame_rate: Fraction) -> str:
    """Render an FCPXML time as HH:MM:SS:FF for logging."""

    total_frames = int(value * frame_rate)
    nominal_fps = round(float(frame_rate))

    frames = total_frames % nominal_fps
    total_seconds = total_frames // nominal_fps
    seconds = total_seconds % 60
    total_minutes = total_seconds // 60
    minutes = total_minutes % 60
    hours = total_minutes // 60

    return f"{hours:02d}:{minutes:02d}:{seconds:02d}:{frames:02d}"


def extract_time_denominator(time_value: Optional[str], fallback: int) -> int:
    """Get the denominator from an existing FCPXML time string."""

    if not time_value:
        return fallback

    text = time_value.strip()
    if text.endswith("s") and "/" in text:
        return int(text[:-1].split("/", 1)[1])

    return fallback


def parse_format_resources(root: ET.Element) -> Dict[str, FormatInfo]:
    """Read FCPXML <format> resources into a lookup keyed by format id."""

    formats: Dict[str, FormatInfo] = {}
    for element in iter_elements_named(root, "format"):
        format_id = element.get("id")
        if not format_id:
            continue

        frame_duration_text = element.get("frameDuration")
        frame_rate_text = element.get("frameRate")

        if frame_duration_text:
            frame_duration = parse_fcpxml_time(frame_duration_text)
            frame_rate = Fraction(1, 1) / frame_duration
            denominator = extract_time_denominator(frame_duration_text, frame_rate.numerator)
        elif frame_rate_text:
            frame_rate = parse_rational(frame_rate_text)
            frame_duration = Fraction(1, 1) / frame_rate
            denominator = frame_rate.numerator
        else:
            continue

        formats[format_id] = FormatInfo(
            frame_rate=frame_rate,
            frame_duration=frame_duration,
            time_denominator=denominator,
        )

    return formats


def fcpxml_src_to_filename(src: str, fcpxml_dir: Path) -> str:
    """Return just the filename from an FCPXML src path or URL."""

    return fcpxml_src_to_path(src, fcpxml_dir).name


def fcpxml_src_to_path(src: str, fcpxml_dir: Path) -> Path:
    """Resolve an FCPXML src path or file URL to a local filesystem path."""

    parsed = urlparse(src)
    if parsed.scheme == "file":
        path_text = unquote(parsed.path)
    else:
        path_text = unquote(src)

    path = Path(path_text)
    if not path.is_absolute():
        path = fcpxml_dir / path

    return path


def asset_source(asset: ET.Element) -> Optional[str]:
    """Read an asset media path from either the asset itself or a media-rep child."""

    if asset.get("src"):
        return asset.get("src")

    for child in asset.iter():
        if child is asset:
            continue
        if local_name(child.tag) == "media-rep" and child.get("src"):
            return child.get("src")

    return None


def collect_mp4_files(folder: Path) -> Dict[str, Path]:
    """Build a case-insensitive filename lookup for MP4 files under folder."""

    files: Dict[str, Path] = {}
    duplicates: Set[str] = set()

    for path in folder.rglob("*"):
        if not path.is_file() or path.suffix.lower() != ".mp4":
            continue

        key = path.name.lower()
        if key in files:
            duplicates.add(key)
            continue
        files[key] = path

    for duplicate in sorted(duplicates):
        print(
            f"WARNING: Duplicate MP4 filename found, skipping ambiguous matches: {duplicate}",
            file=sys.stderr,
        )
        files.pop(duplicate, None)

    return files


def collect_mp4_files_from_xml(root: ET.Element, fcpxml_dir: Path) -> Dict[str, Path]:
    """Build an MP4 lookup from existing media paths already stored in the FCPXML."""

    files: Dict[str, Path] = {}
    for asset in iter_elements_named(root, "asset"):
        src = asset_source(asset)
        if not src:
            continue

        path = fcpxml_src_to_path(src, fcpxml_dir)
        if not path.is_file() or path.suffix.lower() != ".mp4":
            continue

        files.setdefault(path.name.lower(), path)

    return files


def parse_ffprobe_timecode(output: str) -> Optional[str]:
    """Extract a timecode value from ffprobe default output."""

    for line in output.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.lower().endswith("timecode") and value.strip():
            return value.strip()
    return None


def run_ffprobe_timecode(path: Path, show_entry: str, select_video: bool) -> Optional[str]:
    """Run one ffprobe query and return the first discovered timecode."""

    command = [
        FFPROBE_BINARY,
        "-v",
        "quiet",
    ]
    if select_video:
        command.extend(["-select_streams", "v:0"])

    command.extend([
        "-show_entries",
        show_entry,
        "-of",
        "default=noprint_wrappers=1",
        str(path),
    ])

    result = subprocess.run(
        command,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        return None

    return parse_ffprobe_timecode(result.stdout)


def ffprobe_timecode(path: Path) -> Optional[str]:
    """Read embedded timecode from common stream/format locations."""

    queries = [
        ("stream_tags=timecode", True),
        ("format_tags=timecode", True),
        ("stream_tags=timecode", False),
        ("format_tags=timecode", False),
    ]

    for show_entry, select_video in queries:
        timecode = run_ffprobe_timecode(path, show_entry, select_video)
        if timecode:
            return timecode

    return None


def build_asset_patches(
    root: ET.Element,
    fcpxml_path: Path,
    mp4_files: Dict[str, Path],
    formats: Dict[str, FormatInfo],
    stats: PatchStats,
) -> Dict[str, AssetPatch]:
    """Match assets to MP4s, read real timecode, and calculate offsets."""

    patches: Dict[str, AssetPatch] = {}
    timecode_cache: Dict[Path, Optional[str]] = {}

    for asset in iter_elements_named(root, "asset"):
        stats.assets_seen += 1
        asset_id = asset.get("id")
        if not asset_id:
            print("WARNING: <asset> without id skipped.", file=sys.stderr)
            continue

        src = asset_source(asset)
        if not src:
            print(f"WARNING: Asset {asset_id} has no src; skipped.", file=sys.stderr)
            continue

        filename = fcpxml_src_to_filename(src, fcpxml_path.parent)
        mp4_path = mp4_files.get(filename.lower())
        if not mp4_path:
            stats.no_match += 1
            continue
        stats.matched_mp4 += 1

        format_id = asset.get("format")
        format_info = formats.get(format_id or "")
        if not format_info:
            stats.no_format += 1
            continue

        if mp4_path not in timecode_cache:
            timecode_cache[mp4_path] = ffprobe_timecode(mp4_path)

        real_tc = timecode_cache[mp4_path]
        if not real_tc:
            stats.no_timecode += 1
            if stats.no_timecode_files is not None:
                stats.no_timecode_files.append(mp4_path.name)
            continue

        original_start_text = asset.get("start")
        denominator = extract_time_denominator(
            original_start_text, format_info.time_denominator
        )

        try:
            original_start = parse_fcpxml_time(original_start_text)
            real_start = timecode_to_fcpxml_time(real_tc, format_info)
        except ValueError as error:
            print(
                f"WARNING: Cannot parse timecode data for {filename}: {error}; skipped.",
                file=sys.stderr,
            )
            continue

        offset = real_start - original_start

        if (real_start * denominator).denominator != 1:
            denominator = format_info.time_denominator
        if (real_start * denominator).denominator != 1:
            print(
                f"WARNING: Cannot exactly represent real TC for {filename}; skipped.",
                file=sys.stderr,
            )
            continue

        patches[asset_id] = AssetPatch(
            asset_id=asset_id,
            filename=filename,
            original_start=original_start,
            real_start=real_start,
            offset=offset,
            original_tc=time_to_display_tc(original_start, format_info.frame_rate),
            real_tc=real_tc,
            denominator=denominator,
        )
        stats.patched += 1

    return patches


def format_shifted_time(old_value: str, shift: Fraction, fallback_denominator: int) -> str:
    """Shift an existing FCPXML time while preserving its denominator when possible."""

    denominator = extract_time_denominator(old_value, fallback_denominator)
    new_value = parse_fcpxml_time(old_value) + shift

    try:
        return format_fcpxml_time(new_value, denominator)
    except ValueError:
        return format_fcpxml_time(new_value, fallback_denominator)


def patch_time_map(element: ET.Element, patch: AssetPatch) -> bool:
    """Shift retime map time points for a source element that references an asset."""

    patched = False
    for child in element:
        if local_name(child.tag) != "timeMap":
            continue

        for time_point in child:
            if local_name(time_point.tag) != "timept":
                continue

            value = time_point.get("value")
            if not value:
                continue

            time_point.set(
                "value",
                format_shifted_time(value, patch.offset, patch.denominator),
            )
            patched = True

    return patched


def patch_timeline_subtree(
    element: ET.Element,
    patches: Dict[str, AssetPatch],
    inherited_offset_shift: Optional[tuple[Fraction, int]] = None,
) -> None:
    """
    Shift source starts for adjusted assets and child offsets inside those clips.

    Durations are intentionally never shifted: they are lengths, not absolute
    timecode values. Top-level timeline offsets are also left alone so the edit
    structure stays in the same place.
    """

    if inherited_offset_shift and element.get("offset") is not None:
        shift, denominator = inherited_offset_shift
        element.set(
            "offset",
            format_shifted_time(element.get("offset") or "0s", shift, denominator),
        )

    child_offset_shift = inherited_offset_shift
    ref = element.get("ref")
    patch = patches.get(ref or "")
    if patch and local_name(element.tag) in SOURCE_START_ELEMENTS:
        if not patch_time_map(element, patch):
            old_start = element.get("start")
            if old_start is not None:
                element.set(
                    "start",
                    format_shifted_time(old_start, patch.offset, patch.denominator),
                )

        child_offset_shift = (patch.offset, patch.denominator)

    for child in element:
        patch_timeline_subtree(child, patches, child_offset_shift)


def patch_referencing_elements(root: ET.Element, patches: Dict[str, AssetPatch]) -> None:
    """Patch timeline/resource elements that reference adjusted assets."""

    for child in root:
        patch_timeline_subtree(child, patches)


def register_default_namespace(root: ET.Element) -> None:
    """Avoid ns0 prefixes when writing FCPXML files with a default namespace."""

    if root.tag.startswith("{"):
        namespace = root.tag[1:].split("}", 1)[0]
        ET.register_namespace("", namespace)


def resolve_fcpxml_input(path: Path) -> Path:
    """Return a concrete XML file, accepting .fcpxmld package folders too."""

    if path.is_file():
        return path

    if path.is_dir():
        candidates = sorted(
            child
            for child in path.rglob("*")
            if child.is_file() and child.suffix.lower() in {".fcpxml", ".xml"}
        )
        if candidates:
            return candidates[0]

    return path


def output_path_for(input_path: Path, requested_output: Optional[Path]) -> Path:
    """Create originalfilename_tc_fixed.fcpxml next to the input file."""

    if requested_output:
        return requested_output.expanduser().resolve()

    output_suffix = ".fcpxml" if input_path.suffix.lower() == ".fcpxmld" else input_path.suffix
    return input_path.with_name(f"{input_path.stem}_tc_fixed{output_suffix}")


def find_ffprobe() -> Optional[str]:
    """Find ffprobe even when a Finder-launched app has a minimal PATH."""

    candidates = [
        os.environ.get("FCPXML_TC_PATCHER_FFPROBE"),
        shutil.which("ffprobe"),
        "/opt/homebrew/bin/ffprobe",
        "/usr/local/bin/ffprobe",
        "/opt/local/bin/ffprobe",
        "/usr/bin/ffprobe",
    ]

    for cellared in sorted(Path("/opt/homebrew/Cellar/ffmpeg").glob("*/bin/ffprobe"), reverse=True):
        candidates.append(str(cellared))
    for cellared in sorted(Path("/usr/local/Cellar/ffmpeg").glob("*/bin/ffprobe"), reverse=True):
        candidates.append(str(cellared))

    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return str(candidate)

    return None


def ensure_ffprobe_available() -> None:
    """Exit with installation guidance if ffprobe is not available."""

    global FFPROBE_BINARY
    ffprobe = find_ffprobe()
    if ffprobe:
        FFPROBE_BINARY = ffprobe
        return

    print(
        "ERROR: ffprobe was not found on PATH.\n"
        "Install FFmpeg, which includes ffprobe, then run this script again.\n"
        "macOS/Homebrew: brew install ffmpeg\n"
        f"PATH checked: {os.environ.get('PATH', '')}\n"
        "Also checked: /opt/homebrew/bin, /usr/local/bin, /opt/local/bin, and Homebrew Cellar paths.\n"
        "Windows: https://ffmpeg.org/download.html\n"
        "Linux: install the ffmpeg package with your distribution's package manager.",
        file=sys.stderr,
    )
    sys.exit(1)


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description="Patch FCPXML timecode starts for Sony XAVC-S MP4 media."
    )
    parser.add_argument("fcpxml", type=Path, help="Path to the FCPXML export")
    parser.add_argument(
        "sony_mp4_folder",
        nargs="?",
        type=Path,
        help="Optional folder containing Sony MP4 files. If omitted, existing FCPXML src paths are used.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Optional output FCPXML path. Defaults to *_tc_fixed.fcpxml next to the input.",
    )
    return parser.parse_args()


def validate_paths(input_path: Path, fcpxml_path: Path, sony_folder: Optional[Path]) -> None:
    """Validate input paths before doing any work."""

    if not fcpxml_path.is_file():
        print(f"ERROR: FCPXML file not found in: {input_path}", file=sys.stderr)
        sys.exit(1)

    if sony_folder is not None and not sony_folder.is_dir():
        print(f"ERROR: Sony MP4 folder not found: {sony_folder}", file=sys.stderr)
        sys.exit(1)


def print_skip_summary(stats: PatchStats) -> None:
    """Log concise skip details after patching."""

    if stats.no_timecode:
        examples = ", ".join(sorted(set(stats.no_timecode_files or []))[:8])
        suffix = f" Examples: {examples}" if examples else ""
        print(
            f"Skipped {stats.no_timecode} matched MP4 asset(s) with no ffprobe timecode."
            f"{suffix}"
        )

    if stats.no_match:
        print(f"Skipped {stats.no_match} asset(s) with no matching MP4 file.")

    if stats.no_format:
        print(f"Skipped {stats.no_format} matched MP4 asset(s) with no usable FCPXML framerate.")


def main() -> int:
    """CLI entry point."""

    args = parse_args()
    input_path = args.fcpxml.expanduser().resolve()
    fcpxml_path = resolve_fcpxml_input(input_path)
    sony_folder = args.sony_mp4_folder.expanduser().resolve() if args.sony_mp4_folder else None

    validate_paths(input_path, fcpxml_path, sony_folder)
    ensure_ffprobe_available()

    tree = ET.parse(fcpxml_path)
    root = tree.getroot()
    register_default_namespace(root)

    mp4_files = collect_mp4_files_from_xml(root, fcpxml_path.parent)
    if sony_folder is not None:
        folder_files = collect_mp4_files(sony_folder)
        folder_files.update(mp4_files)
        mp4_files = folder_files

    if not mp4_files:
        if sony_folder is None:
            print(
                "ERROR: No existing MP4 files were found from the FCPXML src paths. "
                "Run again with the Sony MP4 folder path.",
                file=sys.stderr,
            )
        else:
            print(f"ERROR: No MP4 files found in {sony_folder}", file=sys.stderr)
        return 1

    formats = parse_format_resources(root)
    stats = PatchStats()
    patches = build_asset_patches(root, fcpxml_path, mp4_files, formats, stats)
    if not patches:
        print_skip_summary(stats)
        print(
            "No assets were patched. This usually means the matched clips do not expose "
            "a timecode via ffprobe, or the selected folder does not contain the referenced "
            "Sony MP4 originals.",
            file=sys.stderr,
        )
        return 1

    for asset in iter_elements_named(root, "asset"):
        asset_id = asset.get("id")
        patch = patches.get(asset_id or "")
        if patch:
            asset.set("start", format_fcpxml_time(patch.real_start, patch.denominator))

    patch_referencing_elements(root, patches)

    destination = output_path_for(input_path, args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    tree.write(destination, encoding="utf-8", xml_declaration=True)

    for patch in patches.values():
        print(
            f"{patch.filename}: original TC {patch.original_tc}, "
            f"real TC {patch.real_tc}, offset applied "
            f"{format_fcpxml_time(patch.offset, patch.denominator)}"
        )

    print_skip_summary(stats)
    print(f"Wrote patched FCPXML: {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
