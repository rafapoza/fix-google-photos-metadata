import os
import json
import argparse
import piexif
from datetime import datetime
import pytz
from PIL import Image
import shutil

# Configured path for Docker environment
IMAGES_FOLDER = "/app/media_items"

# Spain timezone
TIMEZONE = pytz.timezone('Europe/Madrid')

# Verbosity levels:
# 0 = silent
# 1 = only missing/unprocessed destination file information
# 2 = full output
DEFAULT_VERBOSITY = 2

def get_old_value(exif_dict, section, tag):
    """Helper to safely read and decode current metadata."""
    try:
        if section in exif_dict and tag in exif_dict[section]:
            value_bytes = exif_dict[section][tag]
            if isinstance(value_bytes, bytes):
                return value_bytes.decode('utf-8', errors='replace')
            return str(value_bytes)
    except Exception:
        pass
    return "[Not set / Empty]"

def convert_timestamp_to_exif(timestamp_str):
    """Convert a Unix timestamp to EXIF format (YYYY:MM:DD HH:MM:SS) in Spain timezone."""
    try:
        timestamp = int(timestamp_str)
        utc_date = datetime.utcfromtimestamp(timestamp)
        utc_date = pytz.UTC.localize(utc_date)
        spain_date = utc_date.astimezone(TIMEZONE)
        return spain_date.strftime("%Y:%m:%d %H:%M:%S")
    except Exception:
        return None

def convert_gps_coordinate(decimal_coord):
    """Convert a decimal coordinate to EXIF GPS format."""
    if decimal_coord is None:
        return None
    
    decimal_coord = float(decimal_coord)
    abs_coord = abs(decimal_coord)
    
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
    except PermissionError:
        pass
    except Exception:
        pass

def is_exif_supported(file_path):
    """Return True for files that may support EXIF metadata."""
    return file_path.lower().endswith(('.jpg', '.jpeg'))

def get_json_base_name(json_name):
    """Extract the media base name from a Google Photos supplemental JSON file."""
    json_name_lower = json_name.lower()
    split_index = json_name_lower.rfind('.supplement')
    if split_index != -1:
        base_name = json_name[:split_index]
    else:
        base_name = json_name[:-5]
    return base_name.rstrip('.')

def find_associated_source_files(files, json_name):
    """Find source files in the same folder matching the base JSON prefix."""
    base_name = get_json_base_name(json_name)
    lower_base = base_name.lower()

    candidate_roots = {base_name}
    
    # If base ends with suffix + extension, detect and add variants
    if lower_base.endswith('.jpg.s'):
        # Remove .s suffix to get the base with .jpg
        candidate_roots.add(base_name[:-2])  # Remove .s
        # Also add without extension
        candidate_roots.add(base_name[:-6])  # Remove .jpg.s
    elif lower_base.endswith('.jpeg.s'):
        # Remove .s suffix to get the base with .jpeg
        candidate_roots.add(base_name[:-2])  # Remove .s
        # Also add without extension
        candidate_roots.add(base_name[:-7])  # Remove .jpeg.s
    elif lower_base.endswith('.jpg'):
        candidate_roots.add(base_name[:-4])
    elif lower_base.endswith('.jpeg'):
        candidate_roots.add(base_name[:-5])
    else:
        candidate_roots.add(f"{base_name}.jpg")
        candidate_roots.add(f"{base_name}.jpeg")

    if lower_base.endswith('.origin'):
        candidate_roots.add(f"{base_name}A")
    elif lower_base.endswith('.origina'):
        candidate_roots.add(base_name[:-1])

    allowed_remainders = {
        '',
        '.jpg', '.jpeg', '.mp4', '.mov', '.mp', '.png', '.heic', '.avi', '.tif', '.tiff',
        'A', 'A.jpg', 'A.jpeg', 'A.mp4', 'A.mov', 'A.mp',
        '-editada', '-editada.jpg', '-editada.jpeg', '-editada.mp4', '-editada.mov',
        '.s', '.s.jpg', '.s.jpeg',  # Short suffix variants
    }

    associated_files = []
    for file_name in files:
        if file_name == json_name or file_name.lower().endswith('.json'):
            continue

        if file_name in candidate_roots:
            associated_files.append(file_name)
            continue

        for root in candidate_roots:
            if file_name.startswith(root):
                remainder = file_name[len(root):]
                if remainder in allowed_remainders:
                    associated_files.append(file_name)
                    break

    return sorted(set(associated_files))

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

    # Walk the folder tree recursively
    for root, dirs, files in os.walk(IMAGES_FOLDER):
        
        # Skip folders that already contain corrected results
        if "corrected" in root:
            continue

        # Filter metadata JSON files in the current folder
        json_files = [f for f in files if f.lower().endswith('.json')]
        processed_images = []
        destination_folder = f"{root} corrected"
        
        for json_name in json_files:
            # Determine the associated image file name(s)
            json_name_lower = json_name.lower()
            json_path = os.path.join(root, json_name)
            candidate_image_names = find_associated_source_files(files, json_name)

            if not candidate_image_names:
                log(f"\n⚠️ No candidate image file found for JSON: {json_name}", 1)
                missing_source_files.append(os.path.relpath(json_path, IMAGES_FOLDER))
                continue

            for candidate_image_name in candidate_image_names:
                source_image_path = os.path.join(root, candidate_image_name)
                total_items_read += 1
                supports_exif = is_exif_supported(source_image_path)
                relative_media = os.path.relpath(source_image_path, IMAGES_FOLDER)

                # Determine the destination folder by appending " corrected" to the current folder name
                destination_folder = f"{root} corrected"
                destination_image_path = os.path.join(destination_folder, candidate_image_name)
                
                log(f"\n📸 Processing: {os.path.relpath(source_image_path, IMAGES_FOLDER)}", 2)
                log(f"   ↳ Destination: {os.path.relpath(destination_image_path, IMAGES_FOLDER)}", 2)
                
                # Ensure the destination folder exists before working
                os.makedirs(destination_folder, exist_ok=True)
                sync_folder_permissions(root, destination_folder)
                processed_images.append((source_image_path, destination_image_path))

                # 1. Read JSON file data
                with open(json_path, 'r', encoding='utf-8') as f:
                    json_data = json.load(f)
                    
                try:
                    # First copy the original image to the destination path to work on it
                    # This preserves the original file intact in its source folder
                    shutil.copy2(source_image_path, destination_image_path)
                    
                    # 2. Try loading EXIF with piexif from the destination image for supported files
                    if supports_exif:
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
                    else:
                        using_piexif = False
                        pil_image = False
                        image = None
                        exif_data = {}

                    if not supports_exif:
                        log(f"   ℹ️ Non-JPEG media file: copying and updating timestamp only...", 2)
                        timestamp_updated = False
                        if "photoTakenTime" in json_data and isinstance(json_data["photoTakenTime"], dict):
                            try:
                                timestamp_str = json_data["photoTakenTime"].get("timestamp")
                                exif_date = convert_timestamp_to_exif(timestamp_str)
                                if exif_date:
                                    date_obj = datetime.strptime(exif_date, "%Y:%m:%d %H:%M:%S")
                                    date_obj_tz = TIMEZONE.localize(date_obj)
                                    timestamp = date_obj_tz.timestamp()
                                    os.utime(destination_image_path, (timestamp, timestamp))
                                    timestamp_updated = True
                                    total_items_modified += 1
                                    log(f"   ✅ Destination file timestamp updated: {exif_date}", 2)
                            except Exception as time_error:
                                log(f"   ❌ Error updating timestamp: {str(time_error)}", 1)
                        if not timestamp_updated:
                            unmodified_media.append(relative_media)
                        continue

                    changes_made = []

                    # --- MAPPINGS ---
                    mappings = [
                        (piexif.ImageIFD.ImageDescription, "descripcion", "Description"),
                        (piexif.ImageIFD.Artist, "autor", "Author/Artist"),
                        (piexif.ExifIFD.DateTimeOriginal, "photoTakenTime", "Capture Date")
                    ]
                    
                    gps_mappings = [
                        ("latitude", "Latitude"),
                        ("longitude", "Longitude"),
                        ("altitude", "Altitude")
                    ]

                    # 3. Process normal fields
                    for exif_field, json_key, readable_name in mappings:
                        if json_key in json_data:
                            if json_key == "photoTakenTime" and isinstance(json_data[json_key], dict):
                                timestamp_str = json_data[json_key].get("timestamp")
                                new_value = convert_timestamp_to_exif(timestamp_str)
                                if new_value is None:
                                    continue
                            else:
                                new_value = str(json_data[json_key])
                            
                            old_value = ""
                            if using_piexif:
                                for section in ["0th", "Exif"]:
                                    if exif_field in exif_dict.get(section, {}):
                                        value_bytes = exif_dict[section][exif_field]
                                        if isinstance(value_bytes, bytes):
                                            old_value = value_bytes.decode('utf-8', errors='replace')
                                        else:
                                            old_value = str(value_bytes)
                                        break
                            elif pil_image:
                                exif_data = image.getexif()
                                if exif_field in exif_data:
                                    old_value = str(exif_data[exif_field])
                            
                            if not old_value:
                                old_value = "[Not set / Empty]"
                            
                            if old_value == new_value:
                                continue
                            
                            if using_piexif:
                                section = "0th" if exif_field in [piexif.ImageIFD.ImageDescription, piexif.ImageIFD.Artist, piexif.ImageIFD.DateTime] else "Exif"
                                if section not in exif_dict:
                                    exif_dict[section] = {}
                                exif_dict[section][exif_field] = new_value.encode('utf-8')
                            elif pil_image:
                                exif_data[exif_field] = new_value
                            
                            if json_key == "photoTakenTime":
                                if using_piexif:
                                    if "0th" not in exif_dict: exif_dict["0th"] = {}
                                    exif_dict["0th"][piexif.ImageIFD.DateTime] = new_value.encode('utf-8')
                                    if "Exif" not in exif_dict: exif_dict["Exif"] = {}
                                    exif_dict["Exif"][piexif.ExifIFD.DateTimeDigitized] = new_value.encode('utf-8')
                                elif pil_image:
                                    exif_data[piexif.ImageIFD.DateTime] = new_value
                                    exif_data[piexif.ExifIFD.DateTimeDigitized] = new_value
                            
                            changes_made.append({"field": readable_name, "before": old_value, "after": new_value})
                    
                    # 4. Process GPS data (skip if 0.0)
                    if "geoData" in json_data and isinstance(json_data["geoData"], dict):
                        geo_data = json_data["geoData"]
                        lat = float(geo_data.get("latitude", 0.0))
                        lon = float(geo_data.get("longitude", 0.0))
                        
                        if abs(lat) < 0.000001 and abs(lon) < 0.000001:
                            log("   ⏭️ GPS data skipped because values are 0.0.", 2)
                        else:
                            for geo_key, readable_name in gps_mappings:
                                if geo_key in geo_data:
                                    value = float(geo_data[geo_key])
                                    if geo_key == "latitude":
                                        if using_piexif:
                                            if "GPS" not in exif_dict: exif_dict["GPS"] = {}
                                            exif_dict["GPS"][piexif.GPSIFD.GPSLatitude] = convert_gps_coordinate(value)
                                            exif_dict["GPS"][piexif.GPSIFD.GPSLatitudeRef] = b'N' if value >= 0 else b'S'
                                        elif pil_image:
                                            exif_data[piexif.GPSIFD.GPSLatitude] = convert_gps_coordinate(value)
                                            exif_data[piexif.GPSIFD.GPSLatitudeRef] = b'N' if value >= 0 else b'S'
                                        changes_made.append({"field": f"GPS - {readable_name}", "before": "[Not set]", "after": f"{abs(value):.6f}°"})
                                    elif geo_key == "longitude":
                                        if using_piexif:
                                            if "GPS" not in exif_dict: exif_dict["GPS"] = {}
                                            exif_dict["GPS"][piexif.GPSIFD.GPSLongitude] = convert_gps_coordinate(value)
                                            exif_dict["GPS"][piexif.GPSIFD.GPSLongitudeRef] = b'E' if value >= 0 else b'W'
                                        elif pil_image:
                                            exif_data[piexif.GPSIFD.GPSLongitude] = convert_gps_coordinate(value)
                                            exif_data[piexif.GPSIFD.GPSLongitudeRef] = b'E' if value >= 0 else b'W'
                                        changes_made.append({"field": f"GPS - {readable_name}", "before": "[Not set]", "after": f"{abs(value):.6f}°"})
                                    elif geo_key == "altitude":
                                        if using_piexif:
                                            if "GPS" not in exif_dict: exif_dict["GPS"] = {}
                                            exif_dict["GPS"][piexif.GPSIFD.GPSAltitude] = convert_gps_coordinate(value)
                                            exif_dict["GPS"][piexif.GPSIFD.GPSAltitudeRef] = b'\x00'
                                        elif pil_image:
                                            exif_data[piexif.GPSIFD.GPSAltitude] = convert_gps_coordinate(value)
                                            exif_data[piexif.GPSIFD.GPSAltitudeRef] = b'\x00'
                                        changes_made.append({"field": f"GPS - {readable_name}", "before": "[Not set]", "after": f"{value}m"})

                    # 5. Save the modified image to the destination path
                    if changes_made:
                        try:
                            if using_piexif:
                                try:
                                    exif_dict.pop("thumbnail", None)
                                    exif_bytes = piexif.dump(exif_dict)
                                except Exception:
                                    exif_dict.pop("1st", None)
                                    exif_dict.pop("Interop", None)
                                    exif_bytes = piexif.dump(exif_dict)
                                
                                piexif.insert(exif_bytes, destination_image_path)
                            elif pil_image:
                                image.save(destination_image_path, "JPEG", exif=exif_data)
                                image.close()
                        except Exception as save_error:
                            log(f"   ❌ Could not write EXIF to destination: {str(save_error)}", 1)
                            if pil_image and image:
                                image.close()
                            continue

                        total_items_modified += 1
                        
                        # Synchronize the physical file timestamp for corrected file
                        for change in changes_made:
                            if change['field'] == 'Capture Date':
                                try:
                                    date_str = change['after']
                                    date_obj = datetime.strptime(date_str, "%Y:%m:%d %H:%M:%S")
                                    date_obj_tz = TIMEZONE.localize(date_obj)
                                    timestamp = date_obj_tz.timestamp()
                                    os.utime(destination_image_path, (timestamp, timestamp))
                                except Exception:
                                    pass
                        
                        for change in changes_made:
                            log(f"       📝 {change['field']}: [{change['before']}] ➔ [{change['after']}]", 2)
                        log(f"   ✅ Image saved to corrected folder!", 2)
                    else:
                        # If no EXIF changes were applied, close the PIL image reader if open
                        if pil_image:
                            image.close()
                        
                        # Still force the correct file timestamp on the copied image if JSON provides it
                        timestamp_updated = False
                        if "photoTakenTime" in json_data and isinstance(json_data["photoTakenTime"], dict):
                            try:
                                timestamp_str = json_data["photoTakenTime"].get("timestamp")
                                exif_date = convert_timestamp_to_exif(timestamp_str)
                                if exif_date:
                                    date_obj = datetime.strptime(exif_date, "%Y:%m:%d %H:%M:%S")
                                    date_obj_tz = TIMEZONE.localize(date_obj)
                                    timestamp = date_obj_tz.timestamp()
                                    os.utime(destination_image_path, (timestamp, timestamp))
                                    timestamp_updated = True
                                    total_items_modified += 1
                            except Exception:
                                pass
                        if not timestamp_updated:
                            unmodified_media.append(relative_media)
                        log(f"   ⏭️ Copied without EXIF changes (already up to date), file timestamp updated.", 2)
                    
                except Exception as e:
                    log(f"   ❌ Error processing this image: {str(e)}", 1)

        # After finishing this folder, ensure every processed image exists in the corrected folder
        for source_path, dest_path in processed_images:
            if not os.path.exists(dest_path):
                relative_dest = os.path.relpath(dest_path, IMAGES_FOLDER)
                log(f"   ⚠️ Missing corrected image, restoring from source: {relative_dest}", 1)
                try:
                    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                    shutil.copy2(source_path, dest_path)
                    log(f"   ✅ Restored missing image: {relative_dest}", 1)
                except Exception as restore_error:
                    log(f"   ❌ Could not restore missing image: {str(restore_error)}", 1)
                    missing_images.append(relative_dest)

        # Also list non-JSON source files that are still missing in the corrected folder
        source_files = [f for f in files if not f.lower().endswith('.json')]
        for file_name in source_files:
            source_file_path = os.path.join(root, file_name)
            destination_file_path = os.path.join(destination_folder, file_name)
            if not os.path.exists(destination_file_path):
                missing_source_files.append(os.path.relpath(source_file_path, IMAGES_FOLDER))

    log(f"\n=== Processing completed ===", 2)
    log(f"Media files read: {total_items_read}", 2)
    log(f"Media files modified: {total_items_modified}", 2)

    if missing_images:
        log("\nMissing corrected media files:", 1)
        for missing in missing_images:
            log(f" - {missing}", 1)

    if unmodified_media:
        log("\nMedia files read but not modified:", 2)
        for item in unmodified_media:
            log(f" - {item}", 2)

    if missing_source_files:
        log("\nSource files missing in corrected folders:", 1)
        for item in missing_source_files:
            log(f" - {item}", 1)

if __name__ == "__main__":
    args = parse_args()
    update_metadata(args.verbose)