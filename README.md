# fix-google-photos-metadata

A small Python utility to restore and sync EXIF metadata for media files downloaded from Google Photos.

When Google Photos exports media, each photo may have an associated supplemental JSON file containing metadata such as capture time, location, and author. This script reads those JSON files and merges the relevant metadata into the corresponding image files in a `corrected` output folder.

## Features

- Detects Google Photos supplemental metadata files.
- Maps JSON data back into image EXIF metadata.
- Supports JPEG images and timestamp correction for other media.
- Creates a parallel `... corrected` folder to preserve originals.
- Handles many filename variants produced by Google Photos exports.

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

## Notes

- Original files are preserved in the source folders.
- Corrected files are written into sibling folders named like `Fotos del 2023 corrected`.
- If a file is already up to date, it will be copied and timestamped accordingly.
- The script is designed to handle many Google Photos JSON naming variants, including `supplemental-met.json`, `supplemental-meta.json`, and other export irregularities.

