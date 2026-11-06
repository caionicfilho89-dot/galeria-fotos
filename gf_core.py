"""Lógica da Galeria de Fotos (sem GUI): varredura de pasta, ordenação, EXIF, GPS,
matemática de orientação, rotação sem perda, cache de miniaturas e utilitários."""
import ctypes
import hashlib
import os
import re
import tempfile
from datetime import datetime

from PIL import Image, ImageOps

try:  # HEIC é opcional
    import pillow_heif
    pillow_heif.register_heif_opener()
    HEIC_OK = True
except Exception:  # pragma: no cover
    HEIC_OK = False

EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
if HEIC_OK:
    EXTS |= {".heic", ".heif"}
JPEG_EXTS = {".jpg", ".jpeg"}
THUMB = 192
SORT_KEYS = ("nome", "data", "tamanho", "exif")
INVALID_CHARS = '\\/:*?"<>|'


class Entry:
    __slots__ = ("path", "name", "size", "mtime")

    def __init__(self, path, size, mtime):
        self.path = path
        self.name = os.path.basename(path)
        self.size = size
        self.mtime = mtime

    def __repr__(self):
        return f"Entry({self.name!r})"


def is_image(path):
    return os.path.splitext(path)[1].lower() in EXTS


def scan_folder(folder):
    """Lista as imagens suportadas da pasta (sem subpastas)."""
    out = []
    try:
        with os.scandir(folder) as it:
            for e in it:
                try:
                    if e.is_file() and is_image(e.name):
                        st = e.stat()
                        out.append(Entry(e.path, st.st_size, st.st_mtime))
                except OSError:
                    continue
    except OSError:
        return []
    return out


def natural_key(s):
    return [int(t) if t.isdigit() else t.casefold() for t in re.split(r"(\d+)", s)]


def sort_entries(entries, key="nome", reverse=False, exif_dates=None):
    """Ordena. key: nome | data (mtime) | tamanho | exif (cai pra mtime se não houver)."""
    exif_dates = exif_dates or {}
    if key == "data":
        kf = lambda e: (e.mtime, natural_key(e.name))
    elif key == "tamanho":
        kf = lambda e: (e.size, natural_key(e.name))
    elif key == "exif":
        def kf(e):
            d = exif_dates.get(e.path)
            return (d.timestamp() if d else e.mtime, natural_key(e.name))
    else:
        kf = lambda e: natural_key(e.name)
    return sorted(entries, key=kf, reverse=reverse)


def filter_entries(entries, text):
    t = (text or "").strip().casefold()
    if not t:
        return list(entries)
    return [e for e in entries if t in e.name.casefold()]


# ---------- cache ----------
def cache_dir():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "GaleriaFotos", "cache")
    os.makedirs(d, exist_ok=True)
    return d


def cache_key(path, mtime, size, thumb=THUMB):
    raw = f"{os.path.abspath(path).lower()}|{int(mtime * 1000)}|{size}|{thumb}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def cache_path(path, mtime, size, base=None):
    return os.path.join(base or cache_dir(), cache_key(path, mtime, size) + ".png")


def clear_cache(base=None):
    d = base or cache_dir()
    n = 0
    for f in os.listdir(d):
        if f.endswith(".png"):
            try:
                os.remove(os.path.join(d, f))
                n += 1
            except OSError:
                pass
    return n


# ---------- orientação ----------
_CW = {1: 6, 6: 3, 3: 8, 8: 1, 2: 7, 7: 4, 4: 5, 5: 2}
_CCW = {v: k for k, v in _CW.items()}
_OPS = {2: Image.Transpose.FLIP_LEFT_RIGHT, 3: Image.Transpose.ROTATE_180,
        4: Image.Transpose.FLIP_TOP_BOTTOM, 5: Image.Transpose.TRANSPOSE,
        6: Image.Transpose.ROTATE_270, 7: Image.Transpose.TRANSVERSE,
        8: Image.Transpose.ROTATE_90}


def rotate_orientation(orient, clockwise=True):
    """Novo valor da tag EXIF Orientation após girar 90° na exibição."""
    if orient not in _CW:
        orient = 1
    return _CW[orient] if clockwise else _CCW[orient]


def apply_orientation(img, orient):
    op = _OPS.get(orient)
    return img.transpose(op) if op is not None else img


def get_orientation(path):
    try:
        with Image.open(path) as im:
            o = im.getexif().get(0x0112, 1)
        return o if o in range(1, 9) else 1
    except Exception:
        return 1


def rotate_jpeg_lossless(path, clockwise=True):
    """Gira JPEG só mudando a tag EXIF Orientation (sem recodificar). Devolve o novo valor."""
    import piexif
    try:
        exif = piexif.load(path)
    except Exception:
        exif = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "Interop": {}, "thumbnail": None}
    cur = exif.get("0th", {}).get(piexif.ImageIFD.Orientation, 1)
    new = rotate_orientation(cur, clockwise)
    exif.setdefault("0th", {})[piexif.ImageIFD.Orientation] = new
    # a miniatura embutida ficaria com orientação própria; remove pra não confundir
    exif["1st"] = {}
    exif["thumbnail"] = None
    data = piexif.dump(exif)
    fd, tmp = tempfile.mkstemp(suffix=".jpg", dir=os.path.dirname(os.path.abspath(path)))
    os.close(fd)
    try:
        piexif.insert(data, path, tmp)
        os.replace(tmp, path)  # mtime muda de propósito: invalida a miniatura em cache
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return new


def can_resave_rotate(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in (".heic", ".heif"):
        return False
    if ext == ".gif":
        try:
            with Image.open(path) as im:
                return not getattr(im, "is_animated", False)
        except Exception:
            return False
    return ext in {".png", ".webp", ".bmp"}


def rotate_resave(path, clockwise=True):
    """Gira os pixels e regrava (PNG/WEBP/BMP/GIF estático). Chamar só após confirmação."""
    with Image.open(path) as im:
        fmt = im.format
        im.load()
        out = im.transpose(Image.Transpose.ROTATE_270 if clockwise else Image.Transpose.ROTATE_90)
    out.save(path, format=fmt)


# ---------- EXIF ----------
def _num(v):
    try:
        return float(v)
    except Exception:
        return None


def parse_exif_date(s):
    if not s:
        return None
    if isinstance(s, bytes):
        s = s.decode("ascii", "ignore")
    s = str(s).strip().strip("\x00")
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s[:19], fmt)
        except ValueError:
            continue
    return None


def gps_to_decimal(dms, ref):
    """((g, m, s), 'N'/'S'/'E'/'W') -> graus decimais."""
    try:
        d, m, s = (float(x) for x in dms)
    except Exception:
        return None
    val = d + m / 60.0 + s / 3600.0
    if str(ref).upper() in ("S", "W"):
        val = -val
    return val


def fmt_exposure(t):
    t = _num(t)
    if not t or t <= 0:
        return None
    if t >= 1:
        return f"{t:g} s"
    return f"1/{round(1 / t)} s"


def read_exif(path):
    """Dicionário com campos já formatados + lat/lon (decimais) se houver GPS."""
    info = {"largura": None, "altura": None, "camera": None, "data": None, "exposicao": None,
            "iso": None, "lente": None, "lat": None, "lon": None, "orientacao": 1}
    try:
        with Image.open(path) as im:
            info["largura"], info["altura"] = im.size
            ex = im.getexif()
            sub = ex.get_ifd(0x8769) if ex else {}
            gps = ex.get_ifd(0x8825) if ex else {}
    except Exception:
        return info
    make = str(ex.get(0x010F, "") or "").strip("\x00 ")
    model = str(ex.get(0x0110, "") or "").strip("\x00 ")
    if model.lower().startswith(make.lower()):
        make = ""
    cam = f"{make} {model}".strip()
    info["camera"] = cam or None
    o = ex.get(0x0112, 1)
    info["orientacao"] = o if o in range(1, 9) else 1
    dt = parse_exif_date(sub.get(0x9003) or sub.get(0x9004) or ex.get(0x0132))
    info["data"] = dt
    parts = []
    fn = _num(sub.get(0x829D))
    ex_t = fmt_exposure(sub.get(0x829A))
    fl = _num(sub.get(0x920A))
    if ex_t:
        parts.append(ex_t)
    if fn:
        parts.append(f"f/{fn:g}")
    if fl:
        parts.append(f"{fl:g} mm")
    info["exposicao"] = "  ·  ".join(parts) or None
    iso = sub.get(0x8827)
    if isinstance(iso, (tuple, list)) and iso:
        iso = iso[0]
    info["iso"] = int(iso) if iso else None
    lens = sub.get(0xA434)
    info["lente"] = str(lens).strip("\x00 ") if lens else None
    if gps.get(2) and gps.get(4):
        lat = gps_to_decimal(gps[2], gps.get(1, "N"))
        lon = gps_to_decimal(gps[4], gps.get(3, "E"))
        if lat is not None and lon is not None and not (lat == 0 and lon == 0):
            info["lat"], info["lon"] = lat, lon
    return info


def exif_date(path):
    try:
        with Image.open(path) as im:
            ex = im.getexif()
            sub = ex.get_ifd(0x8769)
            return parse_exif_date(sub.get(0x9003) or sub.get(0x9004) or ex.get(0x0132))
    except Exception:
        return None


def map_url(lat, lon):
    return f"https://www.openstreetmap.org/?mlat={lat:.6f}&mlon={lon:.6f}#map=16/{lat:.6f}/{lon:.6f}"


def fmt_size(n):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024.0


# ---------- imagens ----------
def load_pil(path):
    """Abre, aplica a orientação EXIF e devolve RGBA (1º quadro se animado)."""
    with Image.open(path) as im:
        im.load()
        o = im.getexif().get(0x0112, 1)
        out = apply_orientation(im, o if o in range(1, 9) else 1)
        return out.convert("RGBA")


def make_thumbnail(path, dest, size=THUMB):
    with Image.open(path) as im:
        if im.format == "JPEG":
            im.draft("RGB", (size * 2, size * 2))
        im.load()
        o = im.getexif().get(0x0112, 1)
        out = apply_orientation(im, o if o in range(1, 9) else 1)
        out = out.convert("RGBA")
    out.thumbnail((size, size), Image.Resampling.LANCZOS)
    tmp = dest + ".tmp"
    out.save(tmp, format="PNG")
    os.replace(tmp, dest)
    return dest


def get_thumbnail(path, mtime, size, base=None):
    """Caminho da miniatura em cache (gera se faltar)."""
    dest = cache_path(path, mtime, size, base)
    if not os.path.exists(dest):
        make_thumbnail(path, dest)
    return dest


# ---------- arquivos ----------
def validate_new_name(old_path, new_name):
    """Devolve (ok, caminho_novo_ou_mensagem)."""
    new_name = (new_name or "").strip()
    if not new_name:
        return False, "O nome não pode ficar vazio."
    if any(c in new_name for c in INVALID_CHARS):
        return False, "O nome não pode ter  \\ / : * ? \" < > |"
    if new_name.endswith(".") or new_name.endswith(" "):
        return False, "O nome não pode terminar com ponto ou espaço."
    old_ext = os.path.splitext(old_path)[1]
    if not os.path.splitext(new_name)[1]:
        new_name += old_ext
    new_path = os.path.join(os.path.dirname(old_path), new_name)
    if os.path.normcase(new_path) != os.path.normcase(old_path) and os.path.exists(new_path):
        return False, "Já existe um arquivo com esse nome."
    return True, new_path


def rename_file(old_path, new_name):
    ok, res = validate_new_name(old_path, new_name)
    if not ok:
        return False, res
    if os.path.normcase(res) != os.path.normcase(old_path) or res != old_path:
        os.rename(old_path, res)
    return True, res


def send_to_trash(path):
    from send2trash import send2trash
    send2trash(os.path.abspath(path))


def set_wallpaper(path):
    """Define o papel de parede (Windows). Devolve True se a chamada deu certo."""
    SPI_SETDESKWALLPAPER, UPDATE = 0x0014, 0x01 | 0x02
    return bool(ctypes.windll.user32.SystemParametersInfoW(
        SPI_SETDESKWALLPAPER, 0, os.path.abspath(path), UPDATE))


def default_folder():
    p = os.path.join(os.path.expanduser("~"), "Pictures")
    return p if os.path.isdir(p) else os.path.expanduser("~")


def add_recent(recents, folder, limit=8):
    folder = os.path.abspath(folder)
    out = [folder] + [r for r in recents if os.path.normcase(r) != os.path.normcase(folder)]
    return out[:limit]


def reveal_command(path):
    return ["explorer", "/select,", os.path.normpath(path)]
