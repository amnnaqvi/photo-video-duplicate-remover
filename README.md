# Photo and Video Duplicate Remover 🖼️🎥

I made this because I was dealing with the same problem myself. After transferring loads of photos and videos from my phone to my laptop, everything turned into a mess of duplicate photos, videos, edited copies, exported versions, re-saved files, and just a lot of random clutter. 
I tried a few duplicate checkers, especially for phone and the built-in iPhone checker, but most of them were either too slow, too aggressive, not accurate enough, or just unclear about what they were doing.

This is a careful Python script that scans a folder of photos and videos, finds duplicates and near-duplicates, and sorts them into separate folders so you can review everything safely.
It does **not delete or modify your original files**. Zero false positives remain.

If you’re trying to clean up a huge messy media folder, hopefully this helps!

You do **not** need to be a programmer to use this.

---

## Output folders

When the script finishes, it creates a folder called:

```text
dedupe_output/
```

Inside it, you will get:

```text
dedupe_output/
├── final_files/      ← best version of each duplicate group + all unique files which didn't have duplicates (you can make this your new final folder)
├── duplicates/       ← lower-quality confirmed duplicates (you can delete this entire folder)
├── review_needed/    ← close matches that should be checked manually; these matches are grouped into subfolders so you only need to check the images within the same subfolder
└── report.csv        ← full audit trail
```

Your original files are not deleted, renamed, moved, or overwritten. You may manually replace the original folder with the final_files folder afterwards.

---

## Features

* **Recursive scan** — checks the whole folder for any images or videos, even if they're in different subfolders
* **Exact duplicate detection** — uses SHA-256 for byte-for-byte matches
* **Near-duplicate image detection** — uses perceptual hashing
* **Near-duplicate video detection** — compares sampled frames
* **Quality-aware keeper selection** — tries to keep the better version
* **Review-safe output** — uncertain matches go to `review_needed/`
* **Dry run mode** — lets you preview results safely
* **CSV report** — logs every decision with a reason
* **Cross-format awareness** — for example, a `.jpg` and `.png` version of the same image can still match
* **Re-run anytime** — deletes the created output folder and remakes it if you ever rerun the code on the same folder.

---

## Supported file types

### Images

* `.jpg`
* `.jpeg`
* `.png`
* `.bmp`
* `.webp`
* `.tif`
* `.tiff`
* `.heic`
* `.heif`
  (`.heic` / `.heif` need an extra optional package)

### Videos

* `.mp4`
* `.mov`
* `.avi`
* `.mkv`
* `.wmv`
* `.m4v`
* `.webm`

---

## Installation

Make sure you have **Python 3.8+** installed.

Open the command prompt on Windows and run this command to install the required packages:

```bash
pip install pillow imagehash opencv-python pillow-heif
```

---

## How to run it

Download and save the script as:

```text
dedupe_gallery.py
```

Then, the py file in your code editor using the run/play button or in the terminal below using:

```bash
python dedupe_gallery.py
```

After running, it will ask you for the folder path.

### Example on Windows

```text
C:\Users\YourName\Pictures
```

### Example on Mac/Linux

```text
/home/yourname/Pictures
```

---

## Configuration

You can edit these settings near the top of the script:

| Setting                    |           Default | What it does                                                            |
| -------------------------- | ----------------: | ----------------------------------------------------------------------- |
| `DRY_RUN`                  |           `False` | If `True`, only writes `report.csv` and does not copy files             |
| `USE_EXACT_HASH`           |            `True` | Uses SHA-256 first to detect exact duplicates                           |
| `USE_EXIF_VETO`            |            `True` | Avoids merging some visually similar images if EXIF timestamps disagree |
| `IMAGE_HASH_THRESHOLD`     |               `2` | Lower = stricter image duplicate matching                               |
| `VIDEO_FRAME_THRESHOLD`    |               `4` | Per-frame similarity threshold for videos                               |
| `VIDEO_DURATION_TOLERANCE` |            `0.20` | Videos must have nearly the same duration to count as duplicates        |
| `VIDEO_SAMPLE_COUNT`       |               `8` | Number of evenly spaced frames sampled from each video                  |
| `VIDEO_MIN_MATCHED_FRAMES` |               `7` | How many sampled frames must match for videos to count as duplicates    |
| `IMAGE_REVIEW_THRESHOLD`   |               `6` | Similar but uncertain image matches go to review                        |
| `VIDEO_REVIEW_MIN_FRAMES`  |               `5` | Borderline video matches go to review                                   |
| `OUTPUT_ROOT_NAME`         | `"dedupe_output"` | Name of the output folder                                               |

---

## Limitations

### No rotation detection

If the same image is rotated, the script may not recognize it as a duplicate.

### Video offset problem

If one video starts a bit later, has an intro, outro, or trim, the sampled frames may not line up, so the duplicate can be missed.

### EXIF veto can miss real duplicates

If metadata was rewritten, stripped, or changed by another app, `USE_EXIF_VETO = True` can block a real duplicate match.

### Quality scoring issue

A bigger file is not always a better file. Upscaled or AI-enhanced copies may be chosen as the keeper.

### Large libraries can be slow

The grouping logic can get slow on very large folders.

---

## License

MIT License.

---

## Contributing

Pull requests and bug reports are welcome!

Useful areas for improvement:

* rotation-aware hashing
* better performance on very large libraries
* RAW image support
* a review GUI for borderline matches
* comparison more/all file formats
