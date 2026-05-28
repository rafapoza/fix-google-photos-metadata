import os
import json
import piexif
from datetime import datetime
import pytz
from PIL import Image
import shutil

# Configured path for Docker environment
IMAGES_FOLDER = "/app/media_items"

# Spain timezone
TIMEZONE = pytz.timezone('Europe/Madrid')

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

def update_metadata():
    if not os.path.exists(IMAGES_FOLDER):
        print(f"❌ Error: folder {IMAGES_FOLDER} does not exist.")
        return

    print(f"=== Starting recursive search in {IMAGES_FOLDER} ===")

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
            split_index = json_name_lower.rfind('.supplement')
            if split_index != -1:
                image_name = json_name[:split_index]
            else:
                image_name = json_name[:-5]
            if image_name.endswith('.'):
                image_name = image_name[:-1]

            candidate_image_names = [image_name]
            lower_image_name = image_name.lower()

            if lower_image_name.endswith('.origin'):
                candidate_image_names.append(f"{image_name}A")
            elif lower_image_name.endswith('.origina'):
                candidate_image_names.append(image_name[:-1])

            if lower_image_name.endswith('.jpg') or lower_image_name.endswith('.jpeg'):
                if lower_image_name.endswith('.jpg'):
                    base_name = image_name[:-4]
                    ext = image_name[-4:]
                else:
                    base_name = image_name[:-5]
                    ext = image_name[-5:]

                if base_name not in candidate_image_names:
                    candidate_image_names.append(base_name)

                if '-editada' not in base_name.lower():
                    edit_name = f"{base_name}-editada{ext}"
                    if edit_name not in candidate_image_names:
                        candidate_image_names.append(edit_name)
                else:
                    original_name = base_name.replace('-editada', '') + ext
                    if original_name not in candidate_image_names:
                        candidate_image_names.append(original_name)

                if base_name.lower().endswith('.origin'):
                    alt_name = f"{base_name}A{ext}"
                    if alt_name not in candidate_image_names:
                        candidate_image_names.append(alt_name)
                elif base_name.lower().endswith('.origina'):
                    alt_name = f"{base_name[:-1]}{ext}"
                    if alt_name not in candidate_image_names:
                        candidate_image_names.append(alt_name)
            else:
                alt_name = f"{image_name}.jpg"
                if alt_name not in candidate_image_names:
                    candidate_image_names.append(alt_name)
                alt_name2 = f"{image_name}.jpeg"
                if alt_name2 not in candidate_image_names:
                    candidate_image_names.append(alt_name2)

                if lower_image_name.endswith('.origin'):
                    alt_name3 = f"{image_name}A.jpg"
                    if alt_name3 not in candidate_image_names:
                        candidate_image_names.append(alt_name3)
                    alt_name4 = f"{image_name}A.jpeg"
                    if alt_name4 not in candidate_image_names:
                        candidate_image_names.append(alt_name4)
                elif lower_image_name.endswith('.origina'):
                    alt_name3 = f"{image_name[:-1]}.jpg"
                    if alt_name3 not in candidate_image_names:
                        candidate_image_names.append(alt_name3)
                    alt_name4 = f"{image_name[:-1]}.jpeg"
                    if alt_name4 not in candidate_image_names:
                        candidate_image_names.append(alt_name4)

            # Add special-case name variants for ORIGIN/ORIGINA pairs
            for candidate in list(candidate_image_names):
                lower_candidate = candidate.lower()
                if lower_candidate.endswith('.origin'):
                    for ext in ['.jpg', '.jpeg']:
                        alt = f"{candidate}A{ext}"
                        if alt not in candidate_image_names:
                            candidate_image_names.append(alt)
                elif lower_candidate.endswith('.origin.jpg'):
                    alt = candidate[:-4] + 'A.jpg'
                    if alt not in candidate_image_names:
                        candidate_image_names.append(alt)
                elif lower_candidate.endswith('.origin.jpeg'):
                    alt = candidate[:-5] + 'A.jpeg'
                    if alt not in candidate_image_names:
                        candidate_image_names.append(alt)

            for candidate_image_name in candidate_image_names:
                source_image_path = os.path.join(root, candidate_image_name)
                json_path = os.path.join(root, json_name)
                if not os.path.exists(source_image_path):
                    continue

                total_items_read += 1
                supports_exif = is_exif_supported(source_image_path)
                relative_media = os.path.relpath(source_image_path, IMAGES_FOLDER)

                # Determine the destination folder by appending " corrected" to the current folder name
                destination_folder = f"{root} corrected"
                destination_image_path = os.path.join(destination_folder, candidate_image_name)
                
                print(f"\n📸 Processing: {os.path.relpath(source_image_path, IMAGES_FOLDER)}")
                print(f"   ↳ Destination: {os.path.relpath(destination_image_path, IMAGES_FOLDER)}")
                
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
                        print(f"   ℹ️ Non-JPEG media file: copying and updating timestamp only...")
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
                                    print(f"   ✅ Destination file timestamp updated: {exif_date}")
                            except Exception as time_error:
                                print(f"   ❌ Error updating timestamp: {str(time_error)}")
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
                            print("   ⏭️ GPS data skipped because values are 0.0.")
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
                            print(f"   ❌ Could not write EXIF to destination: {str(save_error)}")
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
                            print(f"       📝 {change['field']}: [{change['before']}] ➔ [{change['after']}]")
                        print(f"   ✅ Image saved to corrected folder!")
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
                        print(f"   ⏭️ Copied without EXIF changes (already up to date), file timestamp updated.")
                    
                except Exception as e:
                    print(f"   ❌ Error processing this image: {str(e)}")

        # After finishing this folder, ensure every processed image exists in the corrected folder
        for source_path, dest_path in processed_images:
            if not os.path.exists(dest_path):
                relative_dest = os.path.relpath(dest_path, IMAGES_FOLDER)
                print(f"   ⚠️ Missing corrected image, restoring from source: {relative_dest}")
                try:
                    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                    shutil.copy2(source_path, dest_path)
                    print(f"   ✅ Restored missing image: {relative_dest}")
                except Exception as restore_error:
                    print(f"   ❌ Could not restore missing image: {str(restore_error)}")
                    missing_images.append(relative_dest)

        # Also list non-JSON source files that are still missing in the corrected folder
        source_files = [f for f in files if not f.lower().endswith('.json')]
        for file_name in source_files:
            source_file_path = os.path.join(root, file_name)
            destination_file_path = os.path.join(destination_folder, file_name)
            if not os.path.exists(destination_file_path):
                missing_source_files.append(os.path.relpath(source_file_path, IMAGES_FOLDER))

    print(f"\n=== Processing completed ===")
    print(f"Media files read: {total_items_read}")
    print(f"Media files modified: {total_items_modified}")

    if missing_images:
        print("\nMissing corrected media files:")
        for missing in missing_images:
            print(f" - {missing}")

    if unmodified_media:
        print("\nMedia files read but not modified:")
        for item in unmodified_media:
            print(f" - {item}")

    if missing_source_files:
        print("\nSource files missing in corrected folders:")
        for item in missing_source_files:
            print(f" - {item}")

if __name__ == "__main__":
    update_metadata()