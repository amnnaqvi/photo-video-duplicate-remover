"""
Install:
    pip install pillow pillow-heif imagehash opencv-python

Usage:
    python dedupe_gallery.py
    > Enter folder path: C:\\Users\\YourName\\Pictures

Output (written inside the provided folder):
    dedupe_output/
        final_files/     ← best version of every group + all unique files
        duplicates/      ← lower-quality confirmed duplicates
        review_needed/   ← close but not certain — inspect manually
        report.csv       ← full audit trail

DRY_RUN = True  →  writes only report.csv, touches no other files.
"""

import csv
import hashlib
import math
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import cv2
import imagehash
from PIL import Image, ExifTags

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
    HEIC_SUPPORT = True
except ImportError:
    HEIC_SUPPORT = False
    print("[INFO] pillow-heif not installed — .heic/.heif files will be skipped.")
    print("       Install with:  pip install pillow-heif\n")


# CONFIG  — edit these before your run

# Set True to preview what would happen without copying anything.
# Only report.csv is written.
DRY_RUN = False

# SHA-256 pre-pass: zero false positives, fast for exact copies/renames.
USE_EXACT_HASH = True

# EXIF timestamp veto.
# True  → if both files have different EXIF timestamps, never merge them
# (unless the visual hash match is strong enough to be certain).
# Very safe but creates false negatives when apps rewrite metadata,
# exports strip EXIF, or re-saves alter the timestamp.
# False → ignore EXIF entirely (rely only on visual hashes).
USE_EXIF_VETO = True

# Perceptual hash thresholds.
# Lower = fewer false positives (fewer wrong merges).
# Raise only if you're missing obvious duplicates.
IMAGE_HASH_THRESHOLD     = 2    # applies to both pHash and dHash
VIDEO_FRAME_THRESHOLD    = 4    # per-frame tolerance
VIDEO_DURATION_TOLERANCE = 0.20  # seconds

# How many evenly-spaced frames to sample from each video.
# NOTE: works well for straight transcodes of the same clip.
# Will miss duplicates where one copy has an added intro/outro or
# starts a few seconds offset — intentional (keeps FP rate low).
VIDEO_SAMPLE_COUNT       = 8
VIDEO_MIN_MATCHED_FRAMES = 7    # out of VIDEO_SAMPLE_COUNT

# "Close but not certain" window — files here go to review_needed/.
IMAGE_REVIEW_THRESHOLD   = 6    # phash/dhash distance > HASH_THRESHOLD but <= this
VIDEO_REVIEW_MIN_FRAMES  = 5    # matched frames needed to trigger review (< VIDEO_MIN_MATCHED_FRAMES)

OUTPUT_ROOT_NAME = "dedupe_output"


# EXTENSIONS

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
if HEIC_SUPPORT:
    IMAGE_EXTS |= {".heic", ".heif"}

VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".wmv", ".m4v", ".webm"}


# DATA CLASS
@dataclass
class MediaInfo:
    path:               Path
    media_type:         str    # "image" | "video"
    file_size:          int
    width:              int   = 0
    height:             int   = 0
    duration:           float = 0.0
    exact_sha256:       Optional[str]                       = None
    image_phash:        Optional[imagehash.ImageHash]       = None
    image_dhash:        Optional[imagehash.ImageHash]       = None
    video_frame_hashes: Optional[List[imagehash.ImageHash]] = None
    exif_datetime:      Optional[str]                       = None

    @property
    def pixels(self) -> int:
        return self.width * self.height

# UTILITIES
def safe_mkdir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while data := f.read(chunk):
            h.update(data)
    return h.hexdigest()


def unique_dest(dest_dir: Path, name: str) -> Path:
    candidate = dest_dir / name
    if not candidate.exists():
        return candidate
    stem, suffix = Path(name).stem, Path(name).suffix
    i = 2
    while True:
        candidate = dest_dir / f"{stem}__{i}{suffix}"
        if not candidate.exists():
            return candidate
        i += 1


def copy_file(src: Path, dest_dir: Path, dry_run: bool) -> Path:
    dst = unique_dest(dest_dir, src.name)
    if not dry_run:
        safe_mkdir(dest_dir)
        shutil.copy2(src, dst)
    return dst


def get_exif_datetime(img: Image.Image) -> Optional[str]:
    try:
        exif = img.getexif()
        if not exif:
            return None
        tag_map = {v: k for k, v in ExifTags.TAGS.items()}
        for tag_name in ("DateTimeOriginal", "DateTime"):
            tag_id = tag_map.get(tag_name)
            if tag_id and (value := exif.get(tag_id)):
                return str(value)
    except Exception:
        pass
    return None

# ANALYSIS
def analyze_image(path: Path, compute_sha: bool) -> Optional[MediaInfo]:
    try:
        with Image.open(path) as img:
            img = img.convert("RGB")
            w, h = img.size
            ph = imagehash.phash(img)
            dh = imagehash.dhash(img)
            exif_dt = get_exif_datetime(img)
        return MediaInfo(
            path=path, media_type="image",
            file_size=path.stat().st_size,
            width=w, height=h,
            exact_sha256=sha256_file(path) if compute_sha else None,
            image_phash=ph, image_dhash=dh,
            exif_datetime=exif_dt,
        )
    except Exception as e:
        print(f"  [WARN] Skipped image {path.name}: {e}")
        return None


def analyze_video(path: Path, compute_sha: bool) -> Optional[MediaInfo]:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        print(f"  [WARN] Could not open video: {path.name}")
        return None
    try:
        fps         = cap.get(cv2.CAP_PROP_FPS) or 0
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        w           = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h           = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        duration    = (frame_count / fps) if (fps > 0 and frame_count > 0) else 0.0

        if frame_count <= 0:
            print(f"  [WARN] No frames readable: {path.name}")
            return None

        indices = [
            max(0, min(int(frame_count * i / (VIDEO_SAMPLE_COUNT + 1)), frame_count - 1))
            for i in range(1, VIDEO_SAMPLE_COUNT + 1)
        ]
        hashes: List[imagehash.ImageHash] = []
        for idx in indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ok, frame = cap.read()
            if ok and frame is not None:
                pil = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                hashes.append(imagehash.phash(pil))

        min_needed = max(4, VIDEO_SAMPLE_COUNT // 2)
        if len(hashes) < min_needed:
            print(f"  [WARN] Too few readable frames ({len(hashes)}): {path.name}")
            return None

        return MediaInfo(
            path=path, media_type="video",
            file_size=path.stat().st_size,
            width=w, height=h, duration=duration,
            exact_sha256=sha256_file(path) if compute_sha else None,
            video_frame_hashes=hashes,
        )
    except Exception as e:
        print(f"  [WARN] Skipped video {path.name}: {e}")
        return None
    finally:
        cap.release()


def analyze(path: Path, compute_sha: bool) -> Optional[MediaInfo]:
    ext = path.suffix.lower()
    if ext in IMAGE_EXTS:
        return analyze_image(path, compute_sha)
    if ext in VIDEO_EXTS:
        return analyze_video(path, compute_sha)
    return None


# DUPLICATE CLASSIFICATION  →  "duplicate" | "review" | "unique"

def classify_images(a: MediaInfo, b: MediaInfo) -> Tuple[str, str]:
    if a.image_phash is None or b.image_phash is None:
        return "unique", "missing hashes"

    p = a.image_phash - b.image_phash
    d = (a.image_dhash - b.image_dhash) if (a.image_dhash and b.image_dhash) else 999

    # EXIF veto: only block when the visual match is not strong enough
    # to be certain on its own.  A very tight hash match overrides the veto
    # because metadata is frequently rewritten by export tools.
    if USE_EXIF_VETO:
        if a.exif_datetime and b.exif_datetime and a.exif_datetime != b.exif_datetime:
            if not (p <= IMAGE_HASH_THRESHOLD and d <= IMAGE_HASH_THRESHOLD):
                return "unique", (
                    f"EXIF veto ({a.exif_datetime} vs {b.exif_datetime}); "
                    f"phash={p}, dhash={d}"
                )

    if p <= IMAGE_HASH_THRESHOLD and d <= IMAGE_HASH_THRESHOLD:
        return "duplicate", f"image hash match (phash={p}, dhash={d})"

    if p <= IMAGE_REVIEW_THRESHOLD and d <= IMAGE_REVIEW_THRESHOLD:
        return "review", f"close image hash (phash={p}, dhash={d}) — inspect manually"

    return "unique", f"image hash mismatch (phash={p}, dhash={d})"


def classify_videos(a: MediaInfo, b: MediaInfo) -> Tuple[str, str]:
    if a.video_frame_hashes is None or b.video_frame_hashes is None:
        return "unique", "missing frame hashes"

    if abs(a.duration - b.duration) > VIDEO_DURATION_TOLERANCE:
        return "unique", f"duration mismatch ({a.duration:.3f}s vs {b.duration:.3f}s)"

    pairs   = list(zip(a.video_frame_hashes, b.video_frame_hashes))
    dists   = [ha - hb for ha, hb in pairs]
    matched = sum(1 for d in dists if d <= VIDEO_FRAME_THRESHOLD)
    detail  = f"({matched}/{len(dists)} frames matched, dists={dists})"

    if matched >= VIDEO_MIN_MATCHED_FRAMES:
        return "duplicate", f"video frame match {detail}"

    if matched >= VIDEO_REVIEW_MIN_FRAMES:
        return "review", f"borderline video match {detail} — inspect manually"

    return "unique", f"video frame mismatch {detail}"


def classify(a: MediaInfo, b: MediaInfo) -> Tuple[str, str]:
    if a.media_type != b.media_type:
        return "unique", "different media types"

    # Stage 1: exact binary — definitive, skip perceptual checks entirely.
    if (USE_EXACT_HASH
            and a.exact_sha256
            and b.exact_sha256
            and a.exact_sha256 == b.exact_sha256):
        return "duplicate", "exact binary match (SHA-256)"

    if a.media_type == "image":
        return classify_images(a, b)
    return classify_videos(a, b)

# QUALITY SCORING  (used only after duplication is confirmed)
#
# Proxy — not a perfect measure:
# • A larger JPEG is not always better than a smaller PNG.
# • Higher bitrate video can still be a worse encode.
# • Upscaled/denoised files may score higher than cleaner originals.
# Good enough for choosing the keeper once duplication is established.

def quality_score(m: MediaInfo) -> float:
    base       = float(m.pixels)
    size_bonus = math.log2(max(m.file_size, 1))
    if m.media_type == "video":
        bitrate = m.file_size / max(m.duration, 1.0)
        return base * 10 + math.log2(max(bitrate, 1.0)) + size_bonus
    return base * 10 + size_bonus

def better(a: MediaInfo, b: MediaInfo) -> MediaInfo:
    sa, sb = quality_score(a), quality_score(b)
    if sa != sb:
        return a if sa > sb else b
    return a if a.file_size >= b.file_size else b

# FILE DISCOVERY
def find_media(source: Path) -> List[Path]:
    all_exts = IMAGE_EXTS | VIDEO_EXTS
    return [
        p for p in source.rglob("*")
        if p.is_file()
        and OUTPUT_ROOT_NAME not in p.parts
        and p.suffix.lower() in all_exts
    ]

# GROUPING
# Each candidate is compared against the BEST file currently
# representing each group (not just group[0]).  Prevents a
# low-quality first entry acting as a blurry gatekeeper:

# A ~ B  and  B ~ C  but  A !~ C
# → after B joins, representative updates to better(A,B),
# giving C the best comparison target and landing it in the group.

# "review" matches are kept separate — they never pollute confident
# duplicate groups and are copied to review_needed/ for manual inspection.

# Bucketing by hash prefix would speed this up on very large libraries
# but must only be used as a candidate filter, never as the final
# decision — classify() is always the arbiter.

def build_groups(
    items: List[MediaInfo],
) -> Tuple[List[List[MediaInfo]], List[Tuple[MediaInfo, MediaInfo, str]]]:
    """
    Returns:
        groups       — confirmed-duplicate groups (each len ≥ 1)
        review_pairs — (a, b, reason) for borderline cases
    """
    groups:       List[List[MediaInfo]]                  = []
    best:         List[MediaInfo]                        = []
    review_pairs: List[Tuple[MediaInfo, MediaInfo, str]] = []

    for item in items:
        placed = False
        for i, rep in enumerate(best):
            verdict, reason = classify(item, rep)
            if verdict == "duplicate":
                groups[i].append(item)
                best[i] = better(best[i], item)
                placed = True
                break
            if verdict == "review":
                review_pairs.append((item, rep, reason))
                # Leave the file unplaced — it goes to review_needed/.

        if not placed:
            groups.append([item])
            best.append(item)

    return groups, review_pairs

# MAIN PIPELINE
def dedupe_gallery(source_folder: str) -> None:
    source = Path(source_folder).expanduser().resolve()
    if not source.is_dir():
        raise ValueError(f"Not a valid folder: {source}")

    out_root   = source / OUTPUT_ROOT_NAME
    final_dir  = out_root / "final_files"
    dup_dir    = out_root / "duplicates"
    review_dir = out_root / "review_needed"
    report_csv = out_root / "report.csv"

    if DRY_RUN:
        print("[DRY RUN] No files will be copied — only report.csv will be written.\n")

    if out_root.exists():
        print(f"Removing previous output folder: {out_root}")
        shutil.rmtree(out_root)

    safe_mkdir(out_root)  # always needed for report.csv
    if not DRY_RUN:
        safe_mkdir(final_dir)
        safe_mkdir(dup_dir)
        safe_mkdir(review_dir)

    print(f"Scanning: {source}")
    paths = find_media(source)
    print(f"Found {len(paths)} media files.\n")

    # Analysis
    analyzed: List[MediaInfo] = []
    t0 = time.time()
    for i, p in enumerate(paths, 1):
        kind    = "video" if p.suffix.lower() in VIDEO_EXTS else "image"
        elapsed = time.time() - t0
        eta     = (elapsed / i) * (len(paths) - i) if i > 1 else 0
        print(f"  [{i}/{len(paths)}]  {kind:<6}  {p.name}"
              f"  (elapsed {elapsed:.0f}s, ~{eta:.0f}s remaining)")
        info = analyze(p, USE_EXACT_HASH)
        if info:
            analyzed.append(info)

    skipped = len(paths) - len(analyzed)
    print(f"\nAnalyzed {len(analyzed)} files OK"
          + (f", {skipped} skipped." if skipped else ".") + "\n")

    # Grouping
    print("Building duplicate groups…")
    groups, review_pairs = build_groups(analyzed)

    dup_groups    = [g for g in groups if len(g) > 1]
    unique_groups = [g for g in groups if len(g) == 1]
    print(f"  {len(unique_groups)} unique, "
          f"{len(dup_groups)} duplicate groups "
          f"({sum(len(g) for g in dup_groups)} files involved), "
          f"{len(review_pairs)} borderline pairs → review_needed/\n")

    # Copy & report 
    rows: List[dict] = []
    final_count = dup_count = review_count = 0

    for gid, group in enumerate(groups, 1):
        winner = group[0]
        for item in group[1:]:
            winner = better(winner, item)

        winner_out = copy_file(winner.path, final_dir, DRY_RUN)
        final_count += 1

        if len(group) == 1:
            rows.append({
                "group_id":         gid,
                "status":           "unique",
                "kept_file":        str(winner.path),
                "kept_output":      str(winner_out),
                "duplicate_file":   "",
                "duplicate_output": "",
                "reason":           "no duplicate found",
            })
            continue

        for item in group:
            if item.path == winner.path:
                continue
            _, reason = classify(item, winner)
            dup_out = copy_file(item.path, dup_dir, DRY_RUN)
            dup_count += 1
            rows.append({
                "group_id":         gid,
                "status":           "duplicate",
                "kept_file":        str(winner.path),
                "kept_output":      str(winner_out),
                "duplicate_file":   str(item.path),
                "duplicate_output": str(dup_out),
                "reason":           reason,
            })

    # Review pairs — each pair gets its own subfolder so it's immediately
    # obvious which files the algorithm thought were similar.
    # Layout: review_needed/group_001/file_a.jpg
    # file_b.jpg
    review_gid_start = len(groups) + 1
    for offset, (a, b, reason) in enumerate(review_pairs):
        gid        = review_gid_start + offset
        pair_dir   = review_dir / f"group_{offset + 1:03d}"
        a_out = copy_file(a.path, pair_dir, DRY_RUN)
        b_out = copy_file(b.path, pair_dir, DRY_RUN)
        review_count += 1
        rows.append({
            "group_id":         gid,
            "status":           "review",
            "kept_file":        str(a.path),
            "kept_output":      str(a_out),
            "duplicate_file":   str(b.path),
            "duplicate_output": str(b_out),
            "reason":           reason,
        })

    fieldnames = [
        "group_id", "status",
        "kept_file", "kept_output",
        "duplicate_file", "duplicate_output",
        "reason",
    ]
    with report_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    elapsed_total = time.time() - t0
    dry_tag = "  [DRY RUN — nothing was copied]" if DRY_RUN else ""
    print("─" * 60)
    print(f"Done in {elapsed_total:.1f}s{dry_tag}")
    print(f"  final_files/   → {final_count} files")
    print(f"  duplicates/    → {dup_count} files")
    print(f"  review_needed/ → {review_count} pairs")
    print(f"  report.csv     → {report_csv}")
    print("\nYour source folder was NOT modified.")
    if DRY_RUN:
        print("Set DRY_RUN = False and re-run to copy the files.")


if __name__ == "__main__":
    folder = input("Enter folder path: ").strip().strip('"').strip("'")
    dedupe_gallery(folder)