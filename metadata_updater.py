import os
import json
import argparse
import piexif
from datetime import datetime
import pytz
from PIL import Image
import shutil
import re

# Configured path for Docker environment
IMAGES_FOLDER = "/app/media_items"

# Spain timezone
TIMEZONE = pytz.timezone('Europe/Madrid')

# Media file extensions that can be processed by this script
MEDIA_EXTENSIONS = (
    '.jpg', '.jpeg', '.mp4', '.mov', '.mp', '.png', '.heic', '.avi', '.tif', '.tiff', '.webp', '.gif'
)

# Compile regular expressions globally to avoid loop penalties
PXL_PATTERN = re.compile(r'(pxl_\d{8}_\d{9}|pxl_\d{8}_\d{6})')
ARTIFACTS_PATTERN = re.compile(r'\.json$|\.supplemental$|-editada$|-edited$|\(\d+\)$', re.IGNORECASE)

DEFAULT_VERBOSITY = 2

def get_old_value(exif_dict, section, tag):
    """Helper to safely read and decode current metadata."""
    try:
        if section in exif_dict and tag in exif_dict[section]:
            value_bytes = exif_dict[section][tag]
            return value_bytes.decode('utf-8', errors='replace') if isinstance(value_bytes, bytes) else str(value_bytes)
    except Exception:
        pass
    return "[Not set / Empty]"

def convert_timestamp_to_exif(timestamp_str):
    """Convert a Unix timestamp to EXIF format (YYYY:MM:DD HH:MM:SS) in Spain timezone."""
    try:
        timestamp = int(timestamp_str)
        utc_date = datetime.fromtimestamp(timestamp, tz=pytz.utc)
        spain_date = utc_date.astimezone(TIMEZONE)
        return spain_date.strftime("%Y:%m:%d %H:%M:%S")
    except Exception:
        return None

def convert_gps_coordinate(decimal_coord):
    """Convert a decimal coordinate to EXIF GPS format."""
    if decimal_coord is None:
        return None
    abs_coord = abs(float(decimal_coord))
    degrees = int(abs_coord)
    minutes_decimal = (abs_coord - degrees) * 60
    minutes = int(minutes_decimal)
    seconds = (minutes_decimal - minutes) * 60
    return ((degrees, 1), (minutes, 1), (int(seconds * 1000), 1000))

def sync_folder_permissions(source_folder, destination_folder):
    """Copy owner and mode from the source folder to the destination folder."""
    try:
        source_stat = os.stat(source_folder)
        os.chown(destination_folder, source_stat.st_uid, source_stat.st_gid)
        os.chmod(destination_folder, source_stat.st_mode & 0o777)
    except Exception:
        pass

def is_exif_supported(file_path):
    """Return True for files that may support EXIF metadata."""
    return file_path.lower().endswith(('.jpg', '.jpeg'))

def is_media_source_file(file_name):
    """Return True for media files that should be processed. Explicitly ignores JSON files."""
    name_lower = file_name.lower()
    if name_lower.endswith('.json'):
        return False
    return name_lower.endswith(MEDIA_EXTENSIONS)

def clean_google_artifacts(name):
    """Remove all typical Google Takeout suffixes and extensions using precompiled regex."""
    if not name:
        return ""
    name_lower = name.lower()
    
    # Quick removal of known media extensions
    for ext in MEDIA_EXTENSIONS:
        name_lower = name_lower.replace(ext, '')
        
    return ARTIFACTS_PATTERN.sub('', name_lower).strip()

def extract_json_data(json_path):
    """Read JSON file and return the target filename and data dictionary."""
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            json_data = json.load(f)
    except Exception:
        return None, None

    target_filename = None
    if isinstance(json_data, dict):
        for key in ('title', 'fileName', 'filename', 'name'):
            if key in json_data and isinstance(json_data[key], str) and json_data[key].strip():
                target_filename = json_data[key].strip()
                break
    return target_filename, json_data

def parse_args():
    parser = argparse.ArgumentParser(description='Update media metadata from Google Photos supplemental JSON files.')
    parser.add_argument(
        '-v', '--verbose', 
        type=int, 
        choices=[0, 1, 2], 
        default=DEFAULT_VERBOSITY,
        help='Verbosity level: 0 = silent, 1 = only missing/unprocessed destination file info, 2 = full output'
    )
    return parser.parse_args()


def update_metadata(verbosity=DEFAULT_VERBOSITY):
    def log(message, level=2):
        if verbosity >= level:
            print(message)

    if not os.path.exists(IMAGES_FOLDER):
        log(f"❌ Error: folder {IMAGES_FOLDER} does not exist.", 1)
        return

    log(f"=== Starting recursive search in {IMAGES_FOLDER} ===", 2)

    total_items_read = 0
    total_items_modified = 0
    missing_images = []
    unmodified_media = []
    missing_source_files = []
    successfully_processed_rel_paths = set()

    # Static GPS mapping options
    gps_mappings = [("latitude", "Latitude"), ("longitude", "Longitude"), ("altitude", "Altitude")]

    for root, dirs, files in os.walk(IMAGES_FOLDER):
        if "corrected" in root:
            continue

        # 1. FILTER AND PRE-INDEX CURRENT FOLDER (O(1) search optimization)
        json_files = [f for f in files if f.lower().endswith('.json')]
        if not json_files:
            continue

        media_files = [f for f in files if is_media_source_file(f)]
        
        # Indexed mappings for instant searches without nested loops
        clean_media_map = {}
        pxl_media_map = {}
        
        for mf in media_files:
            cm = clean_google_artifacts(mf)
            clean_media_map.setdefault(cm, []).append(mf)
            
            # Index by PXL pattern if available
            pxl_match = PXL_PATTERN.search(cm)
            if pxl_match:
                pxl_media_map.setdefault(pxl_match.group(1), []).append(mf)

        destination_folder = f"{root} corrected"
        destination_folder_created = False
        processed_images = []

        # 2. PROCESS JSONs
        for json_name in json_files:
            json_path = os.path.join(root, json_name)

            target_filename, json_data = extract_json_data(json_path)
            if json_data is None:
                continue

            # Resolve candidates using in-memory indexed maps (Super fast)
            candidate_set = set()
            clean_json = clean_google_artifacts(json_name)
            clean_target = clean_google_artifacts(target_filename) if target_filename else ""

            # Exact matches
            if clean_json in clean_media_map: candidate_set.update(clean_media_map[clean_json])
            if clean_target in clean_media_map: candidate_set.update(clean_media_map[clean_target])

            # Prefix/substring quick matching
            for cm, original_files in clean_media_map.items():
                if (clean_target and (cm.startswith(clean_target) or clean_target.startswith(cm))) or \
                   (cm.startswith(clean_json) or clean_json.startswith(cm)):
                    candidate_set.update(original_files)

            # Match by PXL pattern
            json_pxl = PXL_PATTERN.search(clean_json) or (PXL_PATTERN.search(clean_target) if clean_target else None)
            if json_pxl and json_pxl.group(1) in pxl_media_map:
                candidate_set.update(pxl_media_map[json_pxl.group(1)])

            candidate_image_names = sorted(list(candidate_set))

            if not candidate_image_names:
                if json_name.lower() not in ("metadatos.json", "metadata.json"):
                    missing_source_files.append(os.path.relpath(json_path, IMAGES_FOLDER))
                continue

            # Ensure destination folder is created only once per active directory
            if not destination_folder_created:
                os.makedirs(destination_folder, exist_ok=True)
                sync_folder_permissions(root, destination_folder)
                destination_folder_created = True

            # 3. PROCESS IDENTIFIED IMAGES
            for candidate_image_name in candidate_image_names:
                source_image_path = os.path.join(root, candidate_image_name)
                destination_image_path = os.path.join(destination_folder, candidate_image_name)
                relative_media = os.path.relpath(source_image_path, IMAGES_FOLDER)
                
                total_items_read += 1
                log(f"\n📸 Processing: {relative_media}", 2)
                log(f"   ↳ Destination: {os.path.relpath(destination_image_path, IMAGES_FOLDER)}", 2)

                processed_images.append((source_image_path, destination_image_path))

                try:
                    shutil.copy2(source_image_path, destination_image_path)
                    
                    # Extract unified compatible timestamp block
                    time_data = json_data.get("photoTakenTime") or json_data.get("creationTime") if isinstance(json_data, dict) else None
                    supports_exif = is_exif_supported(source_image_path)
                    
                    # --- GENERIC FILE FLOW (Non-JPEG: GIFs, Videos, etc.) ---
                    if not supports_exif:
                        log(f"   ℹ️ Non-JPEG media file: copying and updating timestamp only...", 2)
                        timestamp_updated = False
                        
                        if time_data and isinstance(time_data, dict):
                            timestamp_str = time_data.get("timestamp")
                            exif_date = convert_timestamp_to_exif(timestamp_str)
                            if exif_date:
                                try:
                                    date_obj = datetime.strptime(exif_date, "%Y:%m:%d %H:%M:%S")
                                    timestamp = TIMEZONE.localize(date_obj).timestamp()
                                    os.utime(destination_image_path, (timestamp, timestamp))
                                    timestamp_updated = True
                                    log(f"   ✅ Destination file timestamp updated: {exif_date}", 2)
                                except Exception as t_err:
                                    log(f"   ❌ Error updating timestamp: {str(t_err)}", 1)
                        
                        total_items_modified += 1
                        successfully_processed_rel_paths.add(relative_media)
                        if not timestamp_updated:
                            log(f"   ℹ️ Copied using current filesystem time (no time key found in JSON).", 2)
                        continue

                    # --- ADVANCED EXIF METADATA FLOW (JPEGs) ---
                    try:
                        exif_dict = piexif.load(destination_image_path)
                        exif_dict.pop("thumbnail", None)
                        using_piexif = True
                    except Exception:
                        using_piexif = False
                        exif_dict = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "Interop": {}}
                    
                    try:
                        image = Image.open(destination_image_path)
                        pil_image = True
                        exif_data = image.getexif()
                    except Exception:
                        pil_image = False
                        image = None
                        exif_data = {}

                    changes_made = []

                    # Validate and Inject Description and Artist
                    for exif_field, json_key, readable_name in [(piexif.ImageIFD.ImageDescription, "descripcion", "Description"), (piexif.ImageIFD.Artist, "autor", "Author/Artist")]:
                        if json_key in json_data:
                            new_value = str(json_data[json_key])
                            old_value = get_old_value(exif_dict, "0th", exif_field) if using_piexif else str(exif_data.get(exif_field, "[Not set / Empty]"))
                            
                            if old_value != new_value:
                                if using_piexif:
                                    exif_dict["0th"][exif_field] = new_value.encode('utf-8')
                                elif pil_image:
                                    exif_data[exif_field] = new_value
                                changes_made.append({"field": readable_name, "before": old_value, "after": new_value})

                    # Validate and Inject EXIF Date
                    if time_data and isinstance(time_data, dict):
                        new_date = convert_timestamp_to_exif(time_data.get("timestamp"))
                        if new_date:
                            old_date = get_old_value(exif_dict, "Exif", piexif.ExifIFD.DateTimeOriginal) if using_piexif else "[Not set]"
                            if old_date != new_date:
                                if using_piexif:
                                    exif_dict["0th"][piexif.ImageIFD.DateTime] = new_date.encode('utf-8')
                                    exif_dict["Exif"][piexif.ExifIFD.DateTimeOriginal] = new_date.encode('utf-8')
                                    exif_dict["Exif"][piexif.ExifIFD.DateTimeDigitized] = new_date.encode('utf-8')
                                elif pil_image:
                                    exif_data[piexif.ImageIFD.DateTime] = new_date
                                    exif_data[piexif.ExifIFD.DateTimeOriginal] = new_date
                                    exif_data[piexif.ExifIFD.DateTimeDigitized] = new_date
                                changes_made.append({"field": "Capture Date", "before": old_date, "after": new_date})
                    
                    # Validate and Inject GPS Coordinates
                    if "geoData" in json_data and isinstance(json_data["geoData"], dict):
                        geo_data = json_data["geoData"]
                        lat, lon = float(geo_data.get("latitude", 0.0)), float(geo_data.get("longitude", 0.0))
                        
                        if abs(lat) > 0.000001 or abs(lon) > 0.000001:
                            for geo_key, readable_name in gps_mappings:
                                if geo_key in geo_data:
                                    val = float(geo_data[geo_key])
                                    if geo_key == "latitude":
                                        if using_piexif:
                                            exif_dict["GPS"][piexif.GPSIFD.GPSLatitude] = convert_gps_coordinate(val)
                                            exif_dict["GPS"][piexif.GPSIFD.GPSLatitudeRef] = b'N' if val >= 0 else b'S'
                                        elif pil_image:
                                            exif_data[piexif.GPSIFD.GPSLatitude] = convert_gps_coordinate(val)
                                            exif_data[piexif.GPSIFD.GPSLatitudeRef] = b'N' if val >= 0 else b'S'
                                        changes_made.append({"field": f"GPS - {readable_name}", "before": "[Not set]", "after": f"{abs(val):.6f}°"})
                                    elif geo_key == "longitude":
                                        if using_piexif:
                                            exif_dict["GPS"][piexif.GPSIFD.GPSLongitude] = convert_gps_coordinate(val)
                                            exif_dict["GPS"][piexif.GPSIFD.GPSLongitudeRef] = b'E' if val >= 0 else b'W'
                                        elif pil_image:
                                            exif_data[piexif.GPSIFD.GPSLongitude] = convert_gps_coordinate(val)
                                            exif_data[piexif.GPSIFD.GPSLongitudeRef] = b'E' if val >= 0 else b'W'
                                        changes_made.append({"field": f"GPS - {readable_name}", "before": "[Not set]", "after": f"{abs(val):.6f}°"})
                                    elif geo_key == "altitude":
                                        if using_piexif:
                                            exif_dict["GPS"][piexif.GPSIFD.GPSAltitude] = convert_gps_coordinate(val)
                                            exif_dict["GPS"][piexif.GPSIFD.GPSAltitudeRef] = b'\x00'
                                        elif pil_image:
                                            exif_data[piexif.GPSIFD.GPSAltitude] = convert_gps_coordinate(val)
                                            exif_data[piexif.GPSIFD.GPSAltitudeRef] = b'\x00'
                                        changes_made.append({"field": f"GPS - {readable_name}", "before": "[Not set]", "after": f"{val}m"})

                    if changes_made:
                        if using_piexif:
                            try:
                                piexif.insert(piexif.dump(exif_dict), destination_image_path)
                            except Exception:
                                exif_dict.pop("1st", None); exif_dict.pop("Interop", None)
                                piexif.insert(piexif.dump(exif_dict), destination_image_path)
                        elif pil_image:
                            image.save(destination_image_path, "JPEG", exif=exif_data)
                            image.close()

                        total_items_modified += 1
                        successfully_processed_rel_paths.add(relative_media)
                        
                        # Synchronize physical file timestamp for modified files
                        for change in changes_made:
                            if change['field'] == 'Capture Date':
                                try:
                                    date_obj = datetime.strptime(change['after'], "%Y:%m:%d %H:%M:%S")
                                    timestamp = TIMEZONE.localize(date_obj).timestamp()
                                    os.utime(destination_image_path, (timestamp, timestamp))
                                except Exception: pass
                        
                        for ch in changes_made:
                            log(f"       📝 {ch['field']}: [{ch['before']}] ➔ [{ch['after']}]", 2)
                        log(f"   ✅ Image saved to corrected folder!", 2)
                    else:
                        if pil_image: image.close()
                        
                        # Update physical timestamp even if internal EXIF metadata was already up to date
                        if time_data and isinstance(time_data, dict):
                            exif_date = convert_timestamp_to_exif(time_data.get("timestamp"))
                            if exif_date:
                                try:
                                    date_obj = datetime.strptime(exif_date, "%Y:%m:%d %H:%M:%S")
                                    timestamp = TIMEZONE.localize(date_obj).timestamp()
                                    os.utime(destination_image_path, (timestamp, timestamp))
                                except Exception: pass
                                
                        total_items_modified += 1
                        successfully_processed_rel_paths.add(relative_media)
                        log(f"   ⏭️ Copied without EXIF changes (already up to date), file timestamp updated.", 2)
                    
                except Exception as e:
                    log(f"   ❌ Error processing this image: {str(e)}", 1)

        # 4. POST-PROCESSING FOLDER VERIFICATION
        for source_path, dest_path in processed_images:
            if not os.path.exists(dest_path):
                relative_dest = os.path.relpath(dest_path, IMAGES_FOLDER)
                log(f"   ⚠️ Missing corrected image, restoring from source: {relative_dest}", 1)
                try:
                    shutil.copy2(source_path, dest_path)
                except Exception:
                    missing_images.append(relative_dest)

        # Report missing or unmodified items based on pre-indexed sets
        for file_name in media_files:
            source_file_path = os.path.join(root, file_name)
            destination_file_path = os.path.join(destination_folder, file_name)
            relative_media = os.path.relpath(source_file_path, IMAGES_FOLDER)
            
            if not os.path.exists(destination_file_path):
                missing_source_files.append(relative_media)
            elif relative_media not in successfully_processed_rel_paths:
                if relative_media not in unmodified_media:
                    unmodified_media.append(relative_media)

    log(f"\n=== Processing completed ===", 2)
    log(f"Media files read: {total_items_read}", 2)
    log(f"Media files modified: {total_items_modified}", 2)

    if missing_images:
        log("\nMissing corrected media files:", 1)
        for missing in missing_images: log(f" - {missing}", 1)

    if unmodified_media:
        log("\nMedia files read but not modified:", 2)
        for item in sorted(list(set(unmodified_media))): log(f" - {item}", 2)

    if missing_source_files:
        log("\nSource files missing in corrected folders:", 1)
        for item in sorted(list(set(missing_source_files))): log(f" - {item}", 1)

if __name__ == "__main__":
    args = parse_args()
    update_metadata(args.verbose)