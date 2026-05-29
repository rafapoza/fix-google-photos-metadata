import importlib
import os
import sys
import tempfile
import types
import unittest

def ensure_available_metadata_module():
    """Load metadata_updater with stubbed optional dependencies if needed."""
    if 'metadata_updater' in sys.modules:
        importlib.reload(sys.modules['metadata_updater'])
        return sys.modules['metadata_updater']

    if 'piexif' not in sys.modules:
        piexif = types.ModuleType('piexif')
        piexif.ImageIFD = types.SimpleNamespace(ImageDescription=270, Artist=315, DateTime=306)
        piexif.ExifIFD = types.SimpleNamespace(DateTimeOriginal=36867, DateTimeDigitized=36868)
        piexif.GPSIFD = types.SimpleNamespace(
            GPSLatitude=2,
            GPSLatitudeRef=1,
            GPSLongitude=4,
            GPSLongitudeRef=3,
            GPSAltitude=6,
            GPSAltitudeRef=5,
        )
        piexif.load = lambda path: {'0th': {}, 'Exif': {}, 'GPS': {}, '1st': {}, 'Interop': {}}
        piexif.dump = lambda data: b''
        piexif.insert = lambda exif_bytes, path: None
        sys.modules['piexif'] = piexif

    if 'pytz' not in sys.modules:
        pytz = types.ModuleType('pytz')

        class DummyTZ:
            def localize(self, dt):
                return dt

        pytz.UTC = DummyTZ()
        pytz.timezone = lambda name: DummyTZ()
        sys.modules['pytz'] = pytz

    if 'PIL' not in sys.modules:
        pil = types.ModuleType('PIL')
        pil.__path__ = []
        sys.modules['PIL'] = pil

    if 'PIL.Image' not in sys.modules:
        pil_image = types.ModuleType('PIL.Image')
        sys.modules['PIL.Image'] = pil_image
        sys.modules['PIL'].Image = pil_image

    return importlib.import_module('metadata_updater')


class MetadataUpdaterTest(unittest.TestCase):
    def setUp(self):
        self.module = ensure_available_metadata_module()

    def test_clean_google_artifacts_basic(self):
        """Verify that standard media extensions are correctly removed."""
        self.assertEqual(self.module.clean_google_artifacts('photo.jpg'), 'photo')
        self.assertEqual(self.module.clean_google_artifacts('FOTO.JPEG'), 'foto')
        self.assertEqual(self.module.clean_google_artifacts('video.mp4'), 'video')

    def test_clean_google_artifacts_edited_and_duplicates(self):
        """Verify aggressive removal of Google Photos edited suffixes and duplicate tags like (1)."""
        self.assertEqual(self.module.clean_google_artifacts('PXL_123-edited.jpg'), 'pxl_123')
        self.assertEqual(self.module.clean_google_artifacts('PXL_123-editada.jpg'), 'pxl_123')
        self.assertEqual(self.module.clean_google_artifacts('photo(1).jpg'), 'photo')
        self.assertEqual(self.module.clean_google_artifacts('image.json'), 'image')

    def test_indexed_resolution_logic(self):
        """Regression test: emulate the new O(1) folder indexing and matching logic."""
        # Mock files present in the source folder
        media_files = [
            'PXL_20231025_173726515.MP',
            'PXL_20231025_173726515.MP.jpg',
            'other.jpg'
        ]
        
        # Build hash maps exactly as the optimized script does
        clean_media_map = {}
        pxl_media_map = {}
        
        for mf in media_files:
            cm = self.module.clean_google_artifacts(mf)
            clean_media_map.setdefault(cm, []).append(mf)
            
            pxl_match = self.module.PXL_PATTERN.search(cm)
            if pxl_match:
                pxl_media_map.setdefault(pxl_match.group(1), []).append(mf)

        # JSON data to be evaluated
        target_filename = 'PXL_20231025_173726515.MP.jpg'
        json_name = 'PXL_20231025_173726515.MP.jpg.json'
        
        # Simulate fast resolution process using in-memory maps
        candidate_set = set()
        clean_json = self.module.clean_google_artifacts(json_name)
        clean_target = self.module.clean_google_artifacts(target_filename)

        if clean_json in clean_media_map: candidate_set.update(clean_media_map[clean_json])
        if clean_target in clean_media_map: candidate_set.update(clean_media_map[clean_target])

        for cm, original_files in clean_media_map.items():
            if (clean_target and (cm.startswith(clean_target) or clean_target.startswith(cm))) or \
               (cm.startswith(clean_json) or clean_json.startswith(cm)):
                candidate_set.update(original_files)

        json_pxl = self.module.PXL_PATTERN.search(clean_json)
        if json_pxl and json_pxl.group(1) in pxl_media_map:
            candidate_set.update(pxl_media_map[json_pxl.group(1)])

        result = sorted(list(candidate_set))
        
        # Both the .MP container and the .MP.jpg file should be resolved as they share the same base
        self.assertEqual(result, [
            'PXL_20231025_173726515.MP',
            'PXL_20231025_173726515.MP.jpg'
        ])

    def test_extract_json_data_from_title(self):
        """Verify that the 'title' field is correctly extracted from JSON files."""
        with tempfile.TemporaryDirectory() as tmp:
            json_path = os.path.join(tmp, 'test.supp.json')
            with open(json_path, 'w', encoding='utf-8') as f:
                f.write('{"title": "_8d60f2ad-aea8-4143-8d7a-204eaa7b593a.jpeg"}')

            filename, data = self.module.extract_json_data(json_path)
            self.assertEqual(filename, '_8d60f2ad-aea8-4143-8d7a-204eaa7b593a.jpeg')
            self.assertEqual(data, {"title": "_8d60f2ad-aea8-4143-8d7a-204eaa7b593a.jpeg"})

    def test_is_media_source_file_ignores_json(self):
        """Ensure the validation function accepts valid media formats and strictly rejects JSON files."""
        self.assertTrue(self.module.is_media_source_file('image.jpg'))
        self.assertTrue(self.module.is_media_source_file('animation.gif'))
        self.assertFalse(self.module.is_media_source_file('metadata.json'))
        self.assertFalse(self.module.is_media_source_file('metadata.json.json'))


if __name__ == '__main__':
    unittest.main()