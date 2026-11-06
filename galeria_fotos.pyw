"""Galeria de Fotos — navega pastas de fotos com miniaturas em cache, visualizador com zoom,
apresentação (slideshow), painel de EXIF/GPS e rotação sem perda em JPEG. Tudo offline.
"""
import os
import subprocess
import sys

from PyQt6.QtCore import (QPoint, QObject, QRunnable, QSettings, QSize, Qt, QThreadPool, QTimer, QUrl,
                          QVariantAnimation, pyqtSignal)
from PyQt6.QtGui import (QColor, QDesktopServices, QIcon, QImage, QMovie, QPainter, QPixmap)
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QFileDialog,
                             QGraphicsPixmapItem, QGraphicsScene, QGraphicsView, QHBoxLayout,
                             QInputDialog, QLabel, QLineEdit, QListView, QMenu, QMessageBox,
                             QPushButton, QSpinBox, QSplitter, QToolButton, QVBoxLayout, QWidget)
from PyQt6.QtGui import QStandardItem, QStandardItemModel

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gf_core as core  # noqa: E402

ACCENT = "#7c5cff"
QSS = """
QWidget { background:#11131a; color:#e7e9f0; font:10pt 'Segoe UI'; }
#title { font:700 16pt 'Segoe UI'; }
#info { color:#8a93a8; }
QLineEdit, QComboBox, QSpinBox { background:#1b202b; border:1px solid #2d3550;
    border-radius:8px; padding:5px; }
QListView { background:#0c0e14; border:1px solid #2d3550; border-radius:10px; }
QListView::item:selected { background:#2d3550; border-radius:8px; }
QPushButton, QToolButton { background:#222a38; border:0; border-radius:10px; padding:8px 12px; }
QPushButton:hover, QToolButton:hover { background:#2d3550; }
#go { background:#7c5cff; color:#fff; font-weight:600; }
#go:hover { background:#8f74ff; }
QGraphicsView { border:0; background:#05060a; }
QMenu { background:#1b202b; border:1px solid #2d3550; }
QMenu::item:selected { background:#7c5cff; }
"""
LABELS = [("nome", "Nome"), ("data", "Data do arquivo"), ("tamanho", "Tamanho"),
          ("exif", "Data EXIF (tirada)")]


def app_icon():
    pm = QPixmap(64, 64)
    pm.fill(QColor("transparent"))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor(ACCENT))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(6, 12, 52, 40, 8, 8)
    p.setBrush(QColor("#11131a"))
    p.drawEllipse(40, 18, 10, 10)
    p.setBrush(QColor("#c9bcff"))
    p.drawPolygon(*[QPoint(*q) for q in [(10, 48), (24, 28), (34, 40), (40, 34), (54, 48)]])
    p.end()
    return QIcon(pm)


def pil_to_pixmap(im):
    data = im.tobytes("raw", "RGBA")
    qi = QImage(data, im.width, im.height, im.width * 4, QImage.Format.Format_RGBA8888).copy()
    return QPixmap.fromImage(qi)


class Signals(QObject):
    thumb = pyqtSignal(int, str, str)    # geração, caminho da foto, caminho da miniatura
    dates = pyqtSignal(int, object)      # geração, {caminho: datetime|None}


class ThumbJob(QRunnable):
    def __init__(self, gen, entry, sig):
        super().__init__()
        self.gen, self.e, self.sig = gen, entry, sig

    def run(self):
        try:
            out = core.get_thumbnail(self.e.path, self.e.mtime, self.e.size)
        except Exception:
            out = ""
        self.sig.thumb.emit(self.gen, self.e.path, out)


class DatesJob(QRunnable):
    def __init__(self, gen, paths, sig):
        super().__init__()
        self.gen, self.paths, self.sig = gen, paths, sig

    def run(self):
        self.sig.dates.emit(self.gen, {p: core.exif_date(p) for p in self.paths})


def info_html(path):
    """HTML do painel de informações (EXIF + GPS)."""
    try:
        st = os.stat(path)
    except OSError:
        return ""
    i = core.read_exif(path)
    rows = [("Arquivo", os.path.basename(path)), ("Tamanho", core.fmt_size(st.st_size))]
    if i["largura"]:
        rows.append(("Dimensões", f"{i['largura']} × {i['altura']} px"))
    rows += [("Câmera", i["camera"]),
             ("Data", i["data"].strftime("%d/%m/%Y %H:%M:%S") if i["data"] else None),
             ("Exposição", i["exposicao"]), ("ISO", i["iso"]), ("Lente", i["lente"])]
    h = "".join(f"<p><span style='color:#8a93a8'>{k}</span><br><b>{v}</b></p>"
                for k, v in rows if v)
    if i["lat"] is not None:
        h += (f"<p><span style='color:#8a93a8'>GPS</span><br>{i['lat']:.5f}, {i['lon']:.5f}<br>"
              f"<a style='color:{ACCENT}' href='{core.map_url(i['lat'], i['lon'])}'>"
              f"Abrir no mapa</a></p>")
    elif not any(v for _, v in rows[2:]):
        h += "<p style='color:#8a93a8'>Sem dados EXIF.</p>"
    return h


class ZoomView(QGraphicsView):
    zoomed = pyqtSignal(float)

    def wheelEvent(self, ev):
        self.zoomed.emit(1.25 if ev.angleDelta().y() > 0 else 0.8)
        ev.accept()


class Viewer(QWidget):
    def __init__(self, gal, entries, idx):
        super().__init__()
        self.gal = gal
        self.entries = list(entries)
        self.idx = idx
        self.current_path = ""
        self.movie = None
        self.fit_mode = True
        self.show_info = False
        self._anim = None
        self.setWindowTitle("Galeria de Fotos")
        self.setWindowIcon(app_icon())
        self.resize(1100, 760)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        mid = QHBoxLayout()
        mid.setSpacing(0)
        self.scene = QGraphicsScene(self)
        self.item = QGraphicsPixmapItem()
        self.item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self.scene.addItem(self.item)
        self.view = ZoomView(self.scene)
        self.view.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.view.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.view.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.view.zoomed.connect(self.zoom_by)
        self.view.mouseDoubleClickEvent = lambda e: self.toggle_fit()
        mid.addWidget(self.view, 1)
        self.panel = QLabel()
        self.panel.setWordWrap(True)
        self.panel.setOpenExternalLinks(False)
        self.panel.linkActivated.connect(lambda u: QDesktopServices.openUrl(QUrl(u)))
        self.panel.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.panel.setFixedWidth(260)
        self.panel.setContentsMargins(12, 12, 12, 12)
        self.panel.hide()
        mid.addWidget(self.panel)
        root.addLayout(mid, 1)

        self.bar = QWidget()
        bl = QHBoxLayout(self.bar)
        bl.setContentsMargins(10, 6, 10, 6)

        def btn(text, fn, tip=""):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(fn)
            bl.addWidget(b)
            return b
        btn("◀", self.prev, "Anterior (←)")
        btn("▶", self.next, "Próxima (→)")
        btn("Ajustar", self.fit, "Ajustar à janela (F)")
        btn("100%", self.zoom_100, "Tamanho real (1)")
        btn("⟲", lambda: self.act(lambda p: self.gal.rotate_path(p, False)), "Girar à esquerda (Ctrl+←)")
        btn("⟳", lambda: self.act(lambda p: self.gal.rotate_path(p, True)), "Girar à direita (Ctrl+→)")
        self.play_btn = btn("▶ Apresentação", self.toggle_slideshow, "Espaço")
        bl.addWidget(QLabel("a cada"))
        self.interval = QSpinBox()
        self.interval.setRange(1, 120)
        self.interval.setValue(4)
        self.interval.setSuffix(" s")
        self.interval.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        bl.addWidget(self.interval)
        self.fade_chk = QCheckBox("fade")
        self.fade_chk.setChecked(True)
        self.fade_chk.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        bl.addWidget(self.fade_chk)
        bl.addStretch(1)
        self.counter = QLabel("")
        self.counter.setObjectName("info")
        bl.addWidget(self.counter)
        btn("ⓘ Info", self.toggle_info, "Painel de informações (I)")
        btn("⛶", self.toggle_fullscreen, "Tela cheia (F11)")
        root.addWidget(self.bar)

        self.slide_timer = QTimer(self)
        self.slide_timer.timeout.connect(lambda: self.next(fade=self.fade_chk.isChecked()))
        self.show_current()

    # --- carregar ---
    def act(self, fn):
        if self.current_path:
            fn(self.current_path)

    def show_current(self, fade=False):
        if not self.entries:
            self.close()
            return
        self.idx = max(0, min(self.idx, len(self.entries) - 1))
        e = self.entries[self.idx]
        self.current_path = e.path
        if self.movie:
            self.movie.stop()
            self.movie = None
        try:
            animated = False
            if e.path.lower().endswith(".gif"):
                from PIL import Image
                with Image.open(e.path) as im:
                    animated = bool(getattr(im, "is_animated", False))
            if animated:
                self.movie = QMovie(e.path)
                self.movie.frameChanged.connect(self._frame)
                self.movie.start()
                pm = self.movie.currentPixmap()
            else:
                pm = pil_to_pixmap(core.load_pil(e.path))
        except Exception:
            pm = QPixmap()
        self.item.setPixmap(pm)
        self.scene.setSceneRect(self.item.boundingRect())
        if self.fit_mode:
            self.fit()
        self.setWindowTitle(f"{e.name} — Galeria de Fotos")
        self.counter.setText(f"{self.idx + 1} / {len(self.entries)}")
        if self.show_info:
            self.panel.setText(info_html(e.path))
        if fade:
            self._fade_in()
        else:
            self.item.setOpacity(1.0)

    def _frame(self, _n):
        if self.movie:
            self.item.setPixmap(self.movie.currentPixmap())

    def _fade_in(self):
        a = QVariantAnimation(self)
        a.setDuration(500)
        a.setStartValue(0.0)
        a.setEndValue(1.0)
        a.valueChanged.connect(lambda v: self.item.setOpacity(float(v)))
        a.start()
        self._anim = a

    def sync(self, entries):
        """Chamado pela galeria depois de mudar arquivos."""
        cur = self.current_path
        self.entries = list(entries)
        for i, e in enumerate(self.entries):
            if e.path == cur:
                self.idx = i
                break
        self.show_current()

    # --- navegação / zoom ---
    def next(self, fade=False):
        if self.entries:
            self.idx = (self.idx + 1) % len(self.entries)
            self.show_current(fade)

    def prev(self):
        if self.entries:
            self.idx = (self.idx - 1) % len(self.entries)
            self.show_current()

    def fit(self):
        self.fit_mode = True
        if not self.item.pixmap().isNull():
            self.view.fitInView(self.item, Qt.AspectRatioMode.KeepAspectRatio)

    def zoom_100(self):
        self.fit_mode = False
        self.view.resetTransform()

    def toggle_fit(self):
        self.zoom_100() if self.fit_mode else self.fit()

    def zoom_by(self, f):
        self.fit_mode = False
        self.view.scale(f, f)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if self.fit_mode:
            self.fit()

    def toggle_info(self):
        self.show_info = not self.show_info
        self.panel.setVisible(self.show_info)
        if self.show_info and self.current_path:
            self.panel.setText(info_html(self.current_path))

    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
            self.bar.show()
        else:
            self.showFullScreen()
            self.bar.hide()

    def toggle_slideshow(self):
        if self.slide_timer.isActive():
            self.slide_timer.stop()
            self.play_btn.setText("▶ Apresentação")
        else:
            self.slide_timer.start(self.interval.value() * 1000)
            self.play_btn.setText("⏸ Pausar")

    def keyPressEvent(self, ev):
        k, mods = ev.key(), ev.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        if k == Qt.Key.Key_Right:
            self.act(lambda p: self.gal.rotate_path(p, True)) if ctrl else self.next()
        elif k == Qt.Key.Key_Left:
            self.act(lambda p: self.gal.rotate_path(p, False)) if ctrl else self.prev()
        elif k == Qt.Key.Key_F11:
            self.toggle_fullscreen()
        elif k == Qt.Key.Key_Escape:
            if self.isFullScreen():
                self.toggle_fullscreen()
            else:
                self.close()
        elif k == Qt.Key.Key_Space:
            self.toggle_slideshow()
        elif k == Qt.Key.Key_F:
            self.fit()
        elif k == Qt.Key.Key_1:
            self.zoom_100()
        elif k == Qt.Key.Key_I:
            self.toggle_info()
        elif k == Qt.Key.Key_Delete:
            self.act(self.gal.trash_path)
        elif k == Qt.Key.Key_F2:
            self.act(self.gal.rename_path)
        elif k == Qt.Key.Key_C and ctrl:
            self.act(self.gal.copy_path)
        else:
            super().keyPressEvent(ev)

    def contextMenuEvent(self, ev):
        if self.current_path:
            self.gal.path_menu(self.current_path).exec(ev.globalPos())

    def closeEvent(self, ev):
        self.slide_timer.stop()
        if self.movie:
            self.movie.stop()
            self.movie.setFileName("")  # solta o arquivo (Windows trava arquivo aberto)
            self.movie = None
        super().closeEvent(ev)


class Galeria(QWidget):
    def __init__(self, settings_group="main"):
        super().__init__()
        self.setWindowTitle("Galeria de Fotos")
        self.setWindowIcon(app_icon())
        self.resize(1200, 760)
        self.setAcceptDrops(True)
        self.settings = QSettings("GaleriaFotos", "Galeria_" + settings_group)
        self.recents = [r for r in (self.settings.value("recents", []) or []) if os.path.isdir(r)]
        self.folder = ""
        self.all_entries = []
        self.visible = []
        self.icons = {}        # caminho -> (chave de cache, QIcon)
        self.exif_dates = {}
        self.thumbs_ready = 0
        self.gen = 0
        self.viewer = None
        self.pool = QThreadPool()
        self.pool.setMaxThreadCount(4)
        self.sig = Signals()
        self.sig.thumb.connect(self._thumb_done)
        self.sig.dates.connect(self._dates_done)
        self.placeholder = QIcon(self._placeholder_pm())

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 10)
        root.addWidget(QLabel("🖼️ Galeria de Fotos", objectName="title"))
        tb = QHBoxLayout()
        b = QPushButton("📂 Abrir pasta")
        b.setObjectName("go")
        b.clicked.connect(self.choose_folder)
        tb.addWidget(b)
        self.recent_btn = QToolButton()
        self.recent_btn.setText("Recentes ▾")
        self.recent_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.recent_menu = QMenu(self)
        self.recent_btn.setMenu(self.recent_menu)
        tb.addWidget(self.recent_btn)
        tb.addWidget(QLabel("Ordenar:"))
        self.sort_combo = QComboBox()
        for k, t in LABELS:
            self.sort_combo.addItem(t, k)
        self.sort_combo.currentIndexChanged.connect(self.on_sort)
        tb.addWidget(self.sort_combo)
        self.rev_chk = QCheckBox("inverter")
        self.rev_chk.toggled.connect(self.on_sort)
        tb.addWidget(self.rev_chk)
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filtrar pelo nome…")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(self.populate)
        tb.addWidget(self.filter_edit, 1)
        sb = QPushButton("▶ Apresentação")
        sb.clicked.connect(self.start_slideshow)
        tb.addWidget(sb)
        more = QToolButton()
        more.setText("⋯")
        more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        mm = QMenu(self)
        mm.addAction("Limpar cache de miniaturas", self.clear_cache)
        mm.addAction("Atualizar pasta", self.refresh)
        more.setMenu(mm)
        tb.addWidget(more)
        root.addLayout(tb)

        split = QSplitter()
        self.model = QStandardItemModel(self)
        self.list = QListView()
        self.list.setViewMode(QListView.ViewMode.IconMode)
        self.list.setResizeMode(QListView.ResizeMode.Adjust)
        self.list.setMovement(QListView.Movement.Static)
        self.list.setUniformItemSizes(True)
        self.list.setIconSize(QSize(core.THUMB, core.THUMB))
        self.list.setGridSize(QSize(core.THUMB + 24, core.THUMB + 44))
        self.list.setSpacing(4)
        self.list.setWordWrap(True)
        self.list.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.list.setModel(self.model)
        self.list.doubleClicked.connect(lambda ix: self.open_viewer(ix.row()))
        self.list.activated.connect(lambda ix: self.open_viewer(ix.row()))
        self.list.selectionModel().currentChanged.connect(lambda ix, _p: self.show_info(ix.row()))
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self.list_menu)
        split.addWidget(self.list)
        self.info_label = QLabel("Abra uma pasta ou arraste uma aqui.")
        self.info_label.setWordWrap(True)
        self.info_label.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.info_label.setContentsMargins(12, 8, 12, 8)
        self.info_label.setMinimumWidth(230)
        self.info_label.linkActivated.connect(lambda u: QDesktopServices.openUrl(QUrl(u)))
        split.addWidget(self.info_label)
        split.setStretchFactor(0, 1)
        split.setSizes([900, 260])
        root.addWidget(split, 1)
        self.status = QLabel("")
        self.status.setObjectName("info")
        root.addWidget(self.status)
        self._fill_recents()

    # --- pasta ---
    @staticmethod
    def _placeholder_pm():
        pm = QPixmap(core.THUMB, core.THUMB)
        pm.fill(QColor("#1b202b"))
        return pm

    def _fill_recents(self):
        self.recent_menu.clear()
        if not self.recents:
            self.recent_menu.addAction("(vazio)").setEnabled(False)
        for r in self.recents:
            self.recent_menu.addAction(r, lambda checked=False, p=r: self.open_folder(p))

    def choose_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Escolher pasta de fotos",
                                             self.folder or core.default_folder())
        if d:
            self.open_folder(d)

    def open_folder(self, folder):
        folder = os.path.abspath(folder)
        if not os.path.isdir(folder):
            return
        self.folder = folder
        self.gen += 1
        self.thumbs_ready = 0
        self.exif_dates = {}
        self.icons = {}
        self.recents = core.add_recent(self.recents, folder)
        self.settings.setValue("recents", self.recents)
        self._fill_recents()
        self.all_entries = core.scan_folder(folder)
        self.setWindowTitle(f"{os.path.basename(folder) or folder} — Galeria de Fotos")
        self.populate()
        self._queue_thumbs()

    def refresh(self, keep=None):
        if not self.folder:
            return
        keep = keep or self.current_path()
        self.all_entries = core.scan_folder(self.folder)
        self.populate()
        self._queue_thumbs()
        if keep:
            for i, e in enumerate(self.visible):
                if e.path == keep:
                    self.select_row(i)
                    break
        if self.viewer is not None and self.viewer.isVisible():
            self.viewer.sync(self.visible)

    def _queue_thumbs(self):
        for e in self.all_entries:
            ic = self.icons.get(e.path)
            if ic and ic[0] == core.cache_key(e.path, e.mtime, e.size):
                continue
            self.pool.start(ThumbJob(self.gen, e, self.sig))

    def _thumb_done(self, gen, path, thumb):
        if gen != self.gen:
            return
        self.thumbs_ready += 1
        if not thumb:
            return
        e = next((x for x in self.all_entries if x.path == path), None)
        if e is None:
            return
        self.icons[path] = (core.cache_key(e.path, e.mtime, e.size), QIcon(thumb))
        for r, v in enumerate(self.visible):
            if v.path == path:
                self.model.item(r).setIcon(self.icons[path][1])
                break

    def on_sort(self, *_):
        if self.sort_combo.currentData() == "exif":
            missing = [e.path for e in self.all_entries if e.path not in self.exif_dates]
            if missing:
                self.pool.start(DatesJob(self.gen, missing, self.sig))
                return
        self.populate()

    def _dates_done(self, gen, dates):
        if gen != self.gen:
            return
        self.exif_dates.update(dates)
        if self.sort_combo.currentData() == "exif":
            self.populate()

    def populate(self, *_):
        keep = self.current_path()
        es = core.filter_entries(self.all_entries, self.filter_edit.text())
        es = core.sort_entries(es, self.sort_combo.currentData() or "nome",
                               self.rev_chk.isChecked(), self.exif_dates)
        self.visible = es
        self.model.clear()
        for e in es:
            it = QStandardItem(e.name)
            it.setToolTip(f"{e.name}\n{core.fmt_size(e.size)}")
            ic = self.icons.get(e.path)
            it.setIcon(ic[1] if ic else self.placeholder)
            it.setEditable(False)
            self.model.appendRow(it)
        self.status.setText(f"{len(es)} foto(s)" +
                            (f" de {len(self.all_entries)}" if len(es) != len(self.all_entries) else "")
                            + (f"  ·  {self.folder}" if self.folder else ""))
        if keep:
            for i, e in enumerate(es):
                if e.path == keep:
                    self.select_row(i)
                    break

    # --- seleção ---
    def current_path(self):
        ix = self.list.currentIndex()
        if ix.isValid() and 0 <= ix.row() < len(self.visible):
            return self.visible[ix.row()].path
        return ""

    def select_row(self, row):
        if 0 <= row < self.model.rowCount():
            self.list.setCurrentIndex(self.model.index(row, 0))
            self.show_info(row)

    def show_info(self, row):
        if 0 <= row < len(self.visible):
            self.info_label.setText(info_html(self.visible[row].path))

    def open_viewer(self, row):
        if not (0 <= row < len(self.visible)):
            return None
        self.viewer = Viewer(self, self.visible, row)
        self.viewer.show()
        return self.viewer

    def start_slideshow(self):
        if not self.visible:
            return
        row = max(0, self.list.currentIndex().row())
        v = self.open_viewer(row)
        v.toggle_slideshow()
        v.showFullScreen()
        v.bar.hide()

    # --- ações ---
    def path_menu(self, path):
        m = QMenu(self)
        m.addAction("Girar 90° à esquerda", lambda: self.rotate_path(path, False))
        m.addAction("Girar 90° à direita", lambda: self.rotate_path(path, True))
        m.addSeparator()
        m.addAction("Copiar imagem", lambda: self.copy_path(path))
        m.addAction("Mostrar no Explorer", lambda: self.reveal_path(path))
        m.addAction("Renomear…", lambda: self.rename_path(path))
        m.addAction("Definir como papel de parede…", lambda: self.wallpaper_path(path))
        m.addSeparator()
        m.addAction("Mandar pra Lixeira…", lambda: self.trash_path(path))
        return m

    def list_menu(self, pos):
        p = self.current_path()
        if p:
            self.path_menu(p).exec(self.list.viewport().mapToGlobal(pos))

    def rotate_path(self, path, clockwise):
        ext = os.path.splitext(path)[1].lower()
        try:
            if ext in core.JPEG_EXTS:
                core.rotate_jpeg_lossless(path, clockwise)
            elif core.can_resave_rotate(path):
                r = QMessageBox.question(
                    self, "Girar imagem",
                    f"Este formato ({ext}) não tem rotação sem perda.\n"
                    "Posso girar os pixels e salvar o arquivo de novo (sobrescreve). Continuar?")
                if r != QMessageBox.StandardButton.Yes:
                    return
                core.rotate_resave(path, clockwise)
            else:
                QMessageBox.information(self, "Girar imagem",
                                        "Não dá pra girar esse arquivo (HEIC ou GIF animado).")
                return
        except Exception as ex:
            QMessageBox.warning(self, "Girar imagem", f"Não consegui girar:\n{ex}")
            return
        self.refresh(keep=path)

    def copy_path(self, path):
        try:
            QApplication.clipboard().setPixmap(pil_to_pixmap(core.load_pil(path)))
            self.status.setText("Imagem copiada.")
        except Exception as ex:
            QMessageBox.warning(self, "Copiar", f"Não consegui copiar:\n{ex}")

    def reveal_path(self, path):
        subprocess.Popen(core.reveal_command(path))

    def rename_path(self, path):
        base = os.path.splitext(os.path.basename(path))[0]
        name, ok = QInputDialog.getText(self, "Renomear", "Novo nome:", text=base)
        if not ok:
            return
        good, res = core.rename_file(path, name)
        if not good:
            QMessageBox.warning(self, "Renomear", res)
            return
        if self.viewer is not None and self.viewer.current_path == path:
            self.viewer.current_path = res
        self.refresh(keep=res)

    def trash_path(self, path):
        r = QMessageBox.question(self, "Lixeira",
                                 f"Mandar pra Lixeira?\n\n{os.path.basename(path)}\n\n"
                                 "(dá pra restaurar pela Lixeira do Windows)")
        if r != QMessageBox.StandardButton.Yes:
            return False
        try:
            core.send_to_trash(path)
        except Exception as ex:
            QMessageBox.warning(self, "Lixeira", f"Não consegui:\n{ex}")
            return False
        self.refresh(keep="")
        return True

    def wallpaper_path(self, path):
        r = QMessageBox.question(self, "Papel de parede",
                                 f"Definir como papel de parede?\n\n{os.path.basename(path)}")
        if r != QMessageBox.StandardButton.Yes:
            return
        if not core.set_wallpaper(path):
            QMessageBox.warning(self, "Papel de parede", "O Windows recusou a mudança.")

    def clear_cache(self):
        n = core.clear_cache()
        self.icons = {}
        self.status.setText(f"Cache limpo ({n} miniaturas).")
        self.refresh()

    # --- arrastar e soltar ---
    def dragEnterEvent(self, ev):
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()

    def dropEvent(self, ev):
        for u in ev.mimeData().urls():
            p = u.toLocalFile()
            if os.path.isdir(p):
                self.open_folder(p)
                return
            if os.path.isfile(p):
                self.open_folder(os.path.dirname(p))
                for i, e in enumerate(self.visible):
                    if os.path.normcase(e.path) == os.path.normcase(os.path.abspath(p)):
                        self.select_row(i)
                        self.open_viewer(i)
                        return
                return

    def closeEvent(self, ev):
        if self.viewer is not None:
            self.viewer.close()
        super().closeEvent(ev)


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(QSS)
    w = Galeria()
    w.show()
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg and os.path.isdir(arg):
        w.open_folder(arg)
    elif arg and os.path.isfile(arg):
        w.open_folder(os.path.dirname(os.path.abspath(arg)))
    else:
        w.open_folder(core.default_folder())
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
