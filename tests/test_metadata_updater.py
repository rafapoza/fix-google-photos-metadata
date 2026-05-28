import importlib
import sys
import types
import unittest


def ensure_available_metadata_module():
    """Load metadata_updater with stubbed optional dependencies if needed."""
    if 'metadata_updater' in sys.modules:
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

    def test_find_associated_source_files_supplementa_json(self):
        files = [
            'PXL_20231119_162459468-EFFECTS.jpg',
            'PXL_20231119_162459468-EFFECTS.jpg.supplementa.json',
        ]

        associated = self.module.find_associated_source_files(files, files[1])
        self.assertEqual(associated, ['PXL_20231119_162459468-EFFECTS.jpg'])

    def test_find_associated_source_files_cover_and_origin_variants(self):
        files = [
            'PXL_20231202_175752644.LONG_EXPOSURE-01.COVER.jpg',
            'PXL_20231202_175752644.LONG_EXPOSURE-01.COVER..json',
            'PXL_20231202_175752644.LONG_EXPOSURE-02.ORIGINA.jpg',
            'PXL_20231202_175752644.LONG_EXPOSURE-02.ORIGIN.json',
        ]

        cover_associated = self.module.find_associated_source_files(
            files, 'PXL_20231202_175752644.LONG_EXPOSURE-01.COVER..json'
        )
        origin_associated = self.module.find_associated_source_files(
            files, 'PXL_20231202_175752644.LONG_EXPOSURE-02.ORIGIN.json'
        )

        self.assertEqual(cover_associated, ['PXL_20231202_175752644.LONG_EXPOSURE-01.COVER.jpg'])
        self.assertEqual(origin_associated, ['PXL_20231202_175752644.LONG_EXPOSURE-02.ORIGINA.jpg'])


    def test_find_associated_source_files_short_suffix_s_json(self):
        files = [
            'PXL_20251213_164958663.PORTRAIT.ORIGINAL.jpg',
            'PXL_20251213_164958663.PORTRAIT.ORIGINAL.jpg.s.json',
        ]

        associated = self.module.find_associated_source_files(
            files, 'PXL_20251213_164958663.PORTRAIT.ORIGINAL.jpg.s.json'
        )
        self.assertEqual(associated, ['PXL_20251213_164958663.PORTRAIT.ORIGINAL.jpg'])


if __name__ == '__main__':
    unittest.main()
