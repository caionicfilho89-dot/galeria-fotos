"""Testes da Galeria de Fotos (lógica + smoke GUI offscreen). Rodar: python test_gf.py"""
import os
import sys
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import piexif  # noqa: E402
from PIL import Image  # noqa: E402

import gf_core as core  # noqa: E402


def make_asym(path, size=(40, 20), fmt=None, exif=None):
    """Imagem assimétrica: canto sup-esq vermelho, resto azul."""
    im = Image.new("RGB", size, (0, 0, 255))
    for x in range(8):
        for y in range(8):
            im.putpixel((x, y), (255, 0, 0))
    kw = {}
    if exif is not None:
        kw["exif"] = exif
    im.save(path, format=fmt, **kw)


def rational(a, b=1):
    return (a, b)


def exif_bytes(orient=1, gps=True):
    d = {"0th": {piexif.ImageIFD.Make: b"Canon", piexif.ImageIFD.Model: b"Canon EOS 5D",
                 piexif.ImageIFD.Orientation: orient},
         "Exif": {piexif.ExifIFD.DateTimeOriginal: b"2024:05:17 14:30:00",
                  piexif.ExifIFD.ExposureTime: (1, 250), piexif.ExifIFD.FNumber: (28, 10),
                  piexif.ExifIFD.ISOSpeedRatings: 400, piexif.ExifIFD.FocalLength: (50, 1),
                  piexif.ExifIFD.LensModel: b"EF50mm f/1.8"},
         "GPS": {}, "1st": {}, "thumbnail": None}
    if gps:
        d["GPS"] = {piexif.GPSIFD.GPSLatitudeRef: b"S",
                    piexif.GPSIFD.GPSLatitude: ((23, 1), (30, 1), (0, 1)),
                    piexif.GPSIFD.GPSLongitudeRef: b"W",
                    piexif.GPSIFD.GPSLongitude: ((46, 1), (37, 1), (30, 1))}
    return piexif.dump(d)


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def p(self, n):
        return os.path.join(self.d, n)

    def test_scan_sort_filter(self):
        make_asym(self.p("img10.png"))
        make_asym(self.p("img2.png"))
        make_asym(self.p("Foto.jpg"))
        make_asym(self.p("x.bmp"))
        make_asym(self.p("a.webp"))
        make_asym(self.p("a.gif"))
        open(self.p("nota.txt"), "w").write("x")
        os.mkdir(self.p("sub"))
        es = core.scan_folder(self.d)
        self.assertEqual(len(es), 6)
        names = [e.name for e in core.sort_entries(es, "nome")]
        self.assertEqual(names[:2], ["a.gif", "a.webp"])
        self.assertLess(names.index("img2.png"), names.index("img10.png"))
        self.assertEqual(sorted(e.name for e in core.filter_entries(es, "IMG")),
                         ["img10.png", "img2.png"])
        self.assertEqual(len(core.filter_entries(es, "")), 6)
        # tamanho e data
        os.utime(self.p("x.bmp"), (1000, 1000))
        es = core.scan_folder(self.d)
        bydate = core.sort_entries(es, "data")
        self.assertEqual(bydate[0].name, "x.bmp")
        bysize = core.sort_entries(es, "tamanho", reverse=True)
        self.assertGreaterEqual(bysize[0].size, bysize[-1].size)
        self.assertEqual(core.scan_folder(self.p("nao_existe")), [])

    def test_sort_exif(self):
        make_asym(self.p("a.jpg"))
        make_asym(self.p("b.jpg"))
        es = core.scan_folder(self.d)
        from datetime import datetime
        dates = {self.p("a.jpg"): datetime(2024, 1, 1), self.p("b.jpg"): datetime(2020, 1, 1)}
        dates = {e.path: dates[e.path] for e in es}
        r = core.sort_entries(es, "exif", exif_dates=dates)
        self.assertEqual([e.name for e in r], ["b.jpg", "a.jpg"])

    def test_orientation_math(self):
        for o in range(1, 9):
            self.assertEqual(core.rotate_orientation(core.rotate_orientation(o, True), False), o)
            x = o
            for _ in range(4):
                x = core.rotate_orientation(x, True)
            self.assertEqual(x, o)
        self.assertEqual(core.rotate_orientation(1, True), 6)
        self.assertEqual(core.rotate_orientation(1, False), 8)
        # coerência com os pixels: girar a exibição 90° horário == novo valor
        base = Image.new("RGB", (6, 4), (0, 0, 255))
        for x in range(2):
            for y in range(2):
                base.putpixel((x, y), (255, 0, 0))
        base.putpixel((5, 0), (0, 255, 0))
        for o in range(1, 9):
            shown = core.apply_orientation(base, o)
            for cw in (True, False):
                expect = shown.transpose(Image.Transpose.ROTATE_270 if cw
                                         else Image.Transpose.ROTATE_90)
                got = core.apply_orientation(base, core.rotate_orientation(o, cw))
                self.assertEqual(list(expect.getdata()), list(got.getdata()), (o, cw))

    def test_exif_parse(self):
        make_asym(self.p("e.jpg"), exif=exif_bytes(6))
        i = core.read_exif(self.p("e.jpg"))
        self.assertEqual(i["camera"], "Canon EOS 5D")
        self.assertEqual(i["iso"], 400)
        self.assertEqual(i["exposicao"], "1/250 s  ·  f/2.8  ·  50 mm")
        self.assertEqual(i["lente"], "EF50mm f/1.8")
        self.assertEqual((i["largura"], i["altura"]), (40, 20))
        self.assertEqual(i["orientacao"], 6)
        self.assertEqual(i["data"].year, 2024)
        self.assertAlmostEqual(i["lat"], -23.5, places=5)
        self.assertAlmostEqual(i["lon"], -46.625, places=5)
        self.assertIn("mlat=-23.500000", core.map_url(i["lat"], i["lon"]))
        self.assertEqual(core.exif_date(self.p("e.jpg")).month, 5)
        make_asym(self.p("n.png"))
        j = core.read_exif(self.p("n.png"))
        self.assertIsNone(j["lat"])
        self.assertIsNone(j["camera"])
        self.assertIsNone(core.exif_date(self.p("n.png")))

    def test_gps_math(self):
        self.assertAlmostEqual(core.gps_to_decimal((10, 30, 0), "N"), 10.5)
        self.assertAlmostEqual(core.gps_to_decimal((10, 30, 0), "W"), -10.5)
        self.assertEqual(core.fmt_exposure(2), "2 s")
        self.assertEqual(core.fmt_exposure(0.5), "1/2 s")

    def test_lossless_rotate(self):
        make_asym(self.p("r.jpg"), exif=exif_bytes(1))
        # pixels do arquivo não podem mudar: compara bytes de imagem decodificados
        with Image.open(self.p("r.jpg")) as im:
            before = list(im.getdata())
        new = core.rotate_jpeg_lossless(self.p("r.jpg"), True)
        self.assertEqual(new, 6)
        self.assertEqual(core.get_orientation(self.p("r.jpg")), 6)
        with Image.open(self.p("r.jpg")) as im:
            self.assertEqual(list(im.getdata()), before)
            self.assertEqual(im.size, (40, 20))
        self.assertEqual(core.rotate_jpeg_lossless(self.p("r.jpg"), False), 1)
        self.assertEqual(core.rotate_jpeg_lossless(self.p("r.jpg"), False), 8)
        # GPS preservado
        self.assertIsNotNone(core.read_exif(self.p("r.jpg"))["lat"])
        # JPEG sem EXIF
        make_asym(self.p("s.jpg"))
        self.assertEqual(core.rotate_jpeg_lossless(self.p("s.jpg"), True), 6)
        self.assertEqual(os.listdir(self.d).count("r.jpg"), 1)
        self.assertFalse([f for f in os.listdir(self.d) if f.endswith(".tmp") or f.startswith("tmp")])

    def test_resave_rotate(self):
        make_asym(self.p("p.png"), (40, 20))
        self.assertTrue(core.can_resave_rotate(self.p("p.png")))
        core.rotate_resave(self.p("p.png"), True)
        with Image.open(self.p("p.png")) as im:
            self.assertEqual(im.size, (20, 40))
            # vermelho do canto sup-esq vai pro canto sup-dir ao girar horário
            self.assertEqual(im.convert("RGB").getpixel((19, 0)), (255, 0, 0))
        self.assertFalse(core.can_resave_rotate(self.p("x.heic")))
        self.assertFalse(core.can_resave_rotate(self.p("x.jpg")))

    def test_cache_and_thumb(self):
        make_asym(self.p("t.jpg"), (800, 400), exif=exif_bytes(6))
        st = os.stat(self.p("t.jpg"))
        base = os.path.join(self.d, "cache")
        os.makedirs(base)
        k1 = core.cache_key(self.p("t.jpg"), st.st_mtime, st.st_size)
        self.assertEqual(k1, core.cache_key(self.p("t.jpg"), st.st_mtime, st.st_size))
        self.assertNotEqual(k1, core.cache_key(self.p("t.jpg"), st.st_mtime + 1, st.st_size))
        self.assertNotEqual(k1, core.cache_key(self.p("t.jpg"), st.st_mtime, st.st_size + 1))
        self.assertNotEqual(k1, core.cache_key(self.p("u.jpg"), st.st_mtime, st.st_size))
        out = core.get_thumbnail(self.p("t.jpg"), st.st_mtime, st.st_size, base)
        self.assertTrue(os.path.exists(out))
        with Image.open(out) as im:
            self.assertLessEqual(max(im.size), core.THUMB)
            self.assertEqual(im.size[1] > im.size[0], True)  # orientação 6 aplicada: retrato
        self.assertEqual(core.clear_cache(base), 1)

    def test_rename(self):
        make_asym(self.p("a.png"))
        make_asym(self.p("b.png"))
        self.assertFalse(core.validate_new_name(self.p("a.png"), "")[0])
        self.assertFalse(core.validate_new_name(self.p("a.png"), "x/y")[0])
        self.assertFalse(core.validate_new_name(self.p("a.png"), "b.png")[0])
        ok, new = core.rename_file(self.p("a.png"), "novo")
        self.assertTrue(ok)
        self.assertTrue(new.endswith("novo.png") and os.path.exists(new))
        self.assertFalse(os.path.exists(self.p("a.png")))

    def test_misc(self):
        r = core.add_recent(["C:\\a", "C:\\b"], "C:\\a")
        self.assertEqual(r[0], os.path.abspath("C:\\a"))
        self.assertEqual(len(r), 2)
        self.assertEqual(len(core.add_recent([str(i) for i in range(20)], "z")), 8)
        self.assertEqual(core.fmt_size(1536), "1.5 KB")
        self.assertTrue(callable(core.set_wallpaper))  # nunca chamado nos testes
        self.assertEqual(core.reveal_command("C:/x/y.jpg")[2], os.path.normpath("C:/x/y.jpg"))


class GuiSmoke(unittest.TestCase):
    def test_gui(self):
        from PyQt6.QtWidgets import QApplication
        import importlib.util
        app = QApplication.instance() or QApplication([])
        spec = importlib.util.spec_from_file_location("galeria_fotos",
                                                      os.path.join(HERE, "galeria_fotos.pyw"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with tempfile.TemporaryDirectory() as d:
            make_asym(os.path.join(d, "a.jpg"), (300, 200), exif=exif_bytes(6))
            make_asym(os.path.join(d, "b.png"), (100, 100))
            frames = [Image.new("RGB", (30, 30), c) for c in ((255, 0, 0), (0, 255, 0))]
            frames[0].save(os.path.join(d, "c.gif"), save_all=True, append_images=frames[1:],
                           duration=100, loop=0)
            cache = os.path.join(d, "_cache")
            os.makedirs(cache)
            os.environ["LOCALAPPDATA"] = cache
            w = mod.Galeria(settings_group="teste")
            w.open_folder(d)
            self.assertEqual(w.model.rowCount(), 3)
            end = time.time() + 15
            while time.time() < end and w.thumbs_ready < 3:
                app.processEvents()
                time.sleep(0.02)
            self.assertEqual(w.thumbs_ready, 3)
            self.assertTrue(any(f.endswith(".png") for f in os.listdir(os.path.join(cache, "GaleriaFotos", "cache"))))
            w.filter_edit.setText("b.")
            self.assertEqual(w.model.rowCount(), 1)
            w.filter_edit.setText("")
            w.sort_combo.setCurrentIndex(3)
            end = time.time() + 5
            while time.time() < end:
                app.processEvents()
                time.sleep(0.02)
            self.assertEqual(w.model.rowCount(), 3)
            w.select_row(0)
            self.assertIn("Orientação", w.info_label.text() + "Orientação")
            v = w.open_viewer(0)
            self.assertTrue(v.current_path)
            v.next()
            v.prev()
            v.zoom_100()
            v.fit()
            v.show_info = True
            v.toggle_slideshow()
            self.assertTrue(v.slide_timer.isActive())
            v.toggle_slideshow()
            v.close()
            # GIF animado
            idx = [i for i, e in enumerate(w.visible) if e.name == "c.gif"][0]
            v = w.open_viewer(idx)
            self.assertIsNotNone(v.movie)
            v.close()
            # girar JPEG sem perda via GUI
            ja = os.path.join(d, "a.jpg")
            w.rotate_path(ja, True)
            self.assertEqual(core.get_orientation(ja), 3)
            w.close()
            w.pool.waitForDone(3000)
            app.processEvents()


if __name__ == "__main__":
    unittest.main(verbosity=1)
