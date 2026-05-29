# fix-google-photos-metadata

A small Python utility to restore and sync EXIF metadata for media files downloaded from Google Photos.

When Google Photos exports media, each photo may have an associated supplemental JSON file containing metadata such as capture time, location, and author. This script reads those JSON files and merges the relevant metadata into the corresponding image files in a `corrected` output folder.

## Features

- Detects Google Photos supplemental metadata files.
- High-performance $O(1)$ in-memory indexing to instantly match JSON files with their respective media assets without nested loop penalties.
- Maps JSON data back into image EXIF metadata (Description, Artist, Capture Date, and GPS Coordinates).
- Supports JPEG images and hardware/filesystem timestamp correction for other media (Videos, GIFs, PNGs).
- Creates a parallel `... corrected` folder to preserve originals and syncs directory permissions.
- Handles many filename variants produced by Google Photos exports (such as `-edited`, `-editada`, and duplicate indices like `(1 `).

## Usage

### Run locally with Python

1. Place your media and JSON files under `media_items/`.
2. Run:
   ```bash
   python3 metadata_updater.py
   ```
3. The script scans `media_items/` recursively and writes corrected files into `... corrected` folders alongside the originals.

### Run with Docker

1. Make sure Docker is installed.
2. Run:
   ```bash
   docker compose up --build
   ```
3. The container mounts `./media_items` to `/app/media_items` and executes `metadata_updater.py` automatically.

### Run tests

If you want to validate the filename matching logic and script imports, run locally:
```bash
python3 -m unittest tests.test_metadata_updater
```

Or run the tests in Docker once the image is built:
```bash
docker compose run --rm metadata-updater python -m unittest tests.test_metadata_updater
```

### Verbosity levels

The script supports three verbosity levels via the `-v` or `--verbose` arguments:

- `0`: silent
- `1`: only information about missing/unprocessed destination images
- `2`: full output (default)

Run locally with a verbosity level:
```bash
python3 metadata_updater.py --verbose 1
```

Run with Docker and a verbosity level:
```bash
docker compose run --rm metadata-updater python metadata_updater.py --verbose 1
```

> Note: running tests or custom commands requires the Docker container to map or include the script structure, which is handled via volume mounts or the `Dockerfile`.

## Notes

- Original files are strictly preserved in their source folders.
- Corrected files are written into sibling folders named like `Fotos del 2023 corrected`.
- If a file is already up to date, it will be copied and its physical filesystem timestamp (`mtime`) will be adjusted to match the capture time.
- The script uses precompiled regular expressions to handle aggressive Google Photos patterns seamlessly.
