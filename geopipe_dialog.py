# -*- coding: utf-8 -*-
"""GeoPipe Dialog — interface principal do pipeline em PyQt5.

Reimplementação fiel de PipelineGUI (Tkinter) para o ambiente QGIS/PyQt5.
Suporta tres modos: Padrão, Declividade e Livre/Personalizado.
A execução dos scripts ocorre dentro de um QgsTask (thread segura).
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from qgis.core import Qgis, QgsApplication, QgsMapLayer, QgsMessageLog, QgsProject, QgsTask
from qgis.PyQt.QtCore import QSettings, Qt, pyqtSignal
from qgis.PyQt.QtGui import QFont, QTextCursor
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSpacerItem,
    QTableWidget,
    QTableWidgetItem,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

_PLUGIN_DIR = Path(__file__).resolve().parent
_SCRIPT_DIR = _PLUGIN_DIR / "scripts"
_LOG_DIR = _PLUGIN_DIR / "logs"

_REQUIRED_LIBS = ["rasterio", "fiona", "geopandas", "numpy", "pandas", "shapely", "pyogrio"]
# Captura padrões "[X/Y]" emitidos por vetorize_multicpu.py
_TILE_PROGRESS_RE = re.compile(r'\[(\d+)/(\d+)\]')


def _find_python() -> str:
    """Localiza o executavel python.exe compativel com o ambiente do QGIS.

    No QGIS/OSGeo4W, sys.executable aponta para qgis-ltr.exe (nao python.exe),
    por isso procuramos o python.exe na hierarquia do OSGeo4W.
    """
    # Se ja for python, usar direto
    exe = Path(sys.executable)
    if exe.stem.lower().startswith("python") and exe.exists():
        return str(exe)

    # Tentar encontrar a partir de sys.exec_prefix
    # No OSGeo4W: sys.exec_prefix = .../OSGeo4W/apps/Python312
    prefix = Path(sys.exec_prefix)
    osgeo4w_root = prefix.parent.parent  # .../OSGeo4W

    candidates = [
        prefix / "python.exe",                          # apps/Python312/python.exe ← interpretador nativo com libs
        osgeo4w_root / "bin" / "python3.exe",           # bin/python3.exe
        osgeo4w_root / "bin" / "python.exe",            # bin/python.exe
        prefix.parent / "bin" / "python.exe",           # apps/bin/python.exe
    ]

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    # Fallback: retornar sys.executable mesmo que seja incorreto
    return sys.executable


# ── Definição das Etapas ───────────────────────────────────────────────
PIPELINE_STEPS = [
    ("vetorize_multicpu.py", "Vetorização Multi-CPU"),
    ("merge_with_attributes.py", "Merge + Tabela de Atributos"),
    ("create_spatial_index.py", "Índice Espacial (.qix)"),
    ("compress_shapefiles.py", "Compactação em Zip"),
    ("prepare_for_upload.py", "Preparar para Upload (shape.zip)"),
]
PIPELINE_STEPS_CUSTOM = [
    ("vetorize_multicpu.py", "Vetorização Multi-CPU"),
    ("merge_custom_attrs.py", "Merge + Atributos (FAIXA/NOME)"),
    ("create_spatial_index.py", "Índice Espacial (.qix)"),
    ("compress_shapefiles.py", "Compactação em Zip"),
    ("prepare_for_upload.py", "Preparar para Upload (shape.zip)"),
]
PIPELINE_STEPS_DINAMICO = [
    ("vetorize_multicpu.py", "Vetorização Multi-CPU"),
    ("merge_dynamic_attrs.py", "Merge + Atributos Dinâmicos"),
    ("create_spatial_index.py", "Índice Espacial (.qix)"),
    ("compress_shapefiles.py", "Compactação em Zip"),
    ("prepare_for_upload.py", "Preparar para Upload (shape.zip)"),
]

DN_CLASSES: dict[int, str] = {
    1: "AGRICULTURA - LAVOURAS ANUAIS",
    2: "AGRICULTURA - LAVOURAS PERENES",
    3: "PASTAGEM CULTIVADA",
    4: "PASTAGEM NATIVA",
    5: "PASTAGEM DEGRADADA",
    6: "SILVICULTURA (FLORESTAS COMERCIAIS)",
    8: "AREA DE PRESERVACAO (RL,APP)",
    9: "LAGOS, LAGOAS",
    10: "CONSTRUCOES E BENFEITORIAS",
}
DN_CLASSES_DECLIVIDADE: dict[int, str] = {
    1: "Apta",
    2: "Restrita",
    3: "Manual",
    4: "Extrema",
    5: "APP Legal",
}
DECLIV_DEFAULTS: dict[int, tuple[str, str]] = {
    1: (
        "0% a 20% - Aprovação Automatica para todas as culturas (Graos, Cana, etc.). Risco agronômico mínimo.",
        "Apta",
    ),
    2: (
        "20% a 45% - Atencao. Apta para Cafe/Perenes, Pecuaria e Silvicultura. Inapta para commodities (Soja/Milho).",
        "Restrita",
    ),
    3: (
        "45% a 75% - Apta com Restricoes. Foco em Agricultura Familiar (Pronaf), Cafe de Montanha e Fruticultura. Inviavel para agricultura intensiva.",
        "Manual",
    ),
    4: (
        "75% a <100% - Recusa Tecnica. Alto risco de inadimplencia por quebra de safra/custo operacional e risco de segurança do trabalho.",
        "Extrema",
    ),
    5: (
        "≥ 100% - Bloqueio Juridico. Financiamento vedado. Area de Preservacao Permanente de Encosta.",
        "APP Legal",
    ),
}


def _kill_process_tree(proc: subprocess.Popen | None) -> None:
    """Termina um processo e todos os seus processos-filhos no Windows/Unix."""
    if proc is None or proc.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                check=False,
            )
        else:
            proc.terminate()
            proc.wait(timeout=2)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _is_task_running(task: QgsTask | None) -> bool:
    """Verifica com segurança se uma QgsTask está ativa, evitando RuntimeError por wrapper C++ deletado."""
    if task is None:
        return False
    try:
        from qgis.PyQt import sip
        if sip.isdeleted(task):
            return False
    except Exception:
        pass
    try:
        return task.status() in (QgsTask.Queued, QgsTask.OnHold, QgsTask.Running)
    except (RuntimeError, ReferenceError):
        return False


class PipelineTask(QgsTask):
    """Executa o pipeline em thread segura do QGIS."""

    log_message = pyqtSignal(str)
    progress_update = pyqtSignal(int, str)
    finished_signal = pyqtSignal(bool)  # True = sucesso
    log_path_signal = pyqtSignal(str)          # caminho do arquivo de log criado
    step_tile_progress = pyqtSignal(int, int)  # (current, total) — progresso intra-etapa

    def __init__(self, selected_steps, env, cleanup, tiles_dir, log_dir: str = ""):
        super().__init__("GeoPipe Pipeline", QgsTask.CanCancel)
        self._selected_steps = selected_steps  # list[(step_num, script, desc)]
        self._env = env
        self._cleanup = cleanup
        self._tiles_dir = tiles_dir
        self._log_dir = Path(log_dir) if log_dir else _LOG_DIR
        self._process: subprocess.Popen | None = None
        self._resultados = []
        self._falhas = 0

    # ------------------------------------------------------------------
    def run(self) -> bool:  # noqa: D102
        """Corpo da task — roda em thread secundária."""
        self._log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = self._log_dir / f"pipeline_{timestamp}.log"
        self.log_path_signal.emit(str(log_path))

        with open(log_path, "w", encoding="utf-8") as log_fh:

            def both(msg: str):
                self.log_message.emit(msg)
                log_fh.write(msg + "\n")
                log_fh.flush()

            num_selected = len(self._selected_steps)
            total = max(s[0] for s in self._selected_steps) if self._selected_steps else 1

            both("=" * 60)
            both("  PIPELINE DE PROCESSAMENTO GEOESPACIAL")
            both(f"  Início: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            both(f"  Etapas: {', '.join(str(s[0]) for s in self._selected_steps)} (de {total})")
            both(f"  Log: {log_path}")
            both("=" * 60)

            total_start = time.time()

            for prog_idx, (step_num, script, descricao) in enumerate(self._selected_steps):
                if self.isCanceled():
                    both("\n⚠️  Cancelamento solicitado pelo usuário.")
                    self._resultados.append((descricao, "CANCELADO", 0))
                    break

                self.progress_update.emit(prog_idx, f"Etapa {step_num}/{total}: {descricao}")

                both(f"\n{'=' * 60}")
                both(f"  ETAPA {step_num}/{total}: {descricao}")
                both(f"  Script: {script}")
                both(f"{'=' * 60}\n")

                step_start = time.time()
                returncode = -1

                try:
                    script_path = _SCRIPT_DIR / script
                    python_exe = _find_python()
                    self._process = subprocess.Popen(
                        [python_exe, "-u", str(script_path)],
                        cwd=str(_SCRIPT_DIR),
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        env=self._env,
                        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                    )

                    for line in self._process.stdout:
                        line = line.rstrip("\n\r")
                        both(f"  {line}")
                        m = _TILE_PROGRESS_RE.search(line)
                        if m:
                            self.step_tile_progress.emit(int(m.group(1)), int(m.group(2)))
                        if self.isCanceled():
                            _kill_process_tree(self._process)
                            break

                    self._process.stdout.close()  # evita ResourceWarning
                    self._process.wait()
                    returncode = self._process.returncode

                except Exception as exc:
                    both(f"  ERRO ao executar {script}: {exc}")
                    returncode = -1

                step_elapsed = time.time() - step_start
                step_min = step_elapsed / 60

                if self.isCanceled():
                    both(f"\n⚠️  Etapa {step_num} cancelada após {step_min:.2f} min.")
                    self._resultados.append((descricao, "CANCELADO", step_min))
                    break
                elif returncode != 0:
                    both(f"\n❌ FALHA na etapa {step_num}: {descricao}")
                    both(f"   Código de saída: {returncode}")
                    self._resultados.append((descricao, "FALHA", step_min))
                    self._falhas += 1
                    break
                else:
                    both(f"\n✅ Etapa {step_num} concluída em {step_min:.2f} min ({step_elapsed:.0f}s)")
                    self._resultados.append((descricao, "OK", step_min))

                self.progress_update.emit(prog_idx + 1, f"Etapa {step_num}/{total} concluída")

            # Resumo
            total_elapsed = time.time() - total_start
            total_min = total_elapsed / 60
            both(f"\n{'=' * 60}")
            both("  RESUMO DO PIPELINE")
            both(f"{'=' * 60}")
            both(f"  {'Etapa':<35} {'Status':<10} {'Tempo':<12}")
            both(f"  {'-'*35} {'-'*10} {'-'*12}")
            for desc, status, mins in self._resultados:
                both(f"  {desc:<35} {status:<10} {mins:.2f} min")
            both(f"  TEMPO TOTAL: {total_min:.2f} min ({total_elapsed:.0f}s)")

            cancelled = any(s == "CANCELADO" for _, s, _ in self._resultados)
            if self._falhas:
                both(f"  ⚠️  Pipeline encerrado com {self._falhas} falha(s).")
            elif cancelled:
                both("  ⚠️  Pipeline cancelado pelo usuário.")
            else:
                both("  🎉 Pipeline completo sem erros!")

            # Limpeza
            if not self._falhas and not cancelled and self._cleanup:
                td = self._tiles_dir.strip()
                if td and Path(td).exists():
                    both(f"\n  🗑️  Limpando tiles: {td}")
                    count = 0
                    for f in Path(td).iterdir():
                        if f.is_file():
                            f.unlink()
                            count += 1
                        elif f.is_dir():
                            shutil.rmtree(f)
                            count += 1
                    both(f"  🗑️  {count} item(ns) removido(s).")

            both(f"\n  Log salvo em: {log_path}")

        self.progress_update.emit(num_selected, "Concluído" if not self._falhas and not cancelled else "Encerrado com erros")
        return self._falhas == 0 and not cancelled

    def finished(self, result: bool):  # noqa: D102
        self.finished_signal.emit(result)

    def cancel(self):  # noqa: D102
        _kill_process_tree(self._process)
        super().cancel()


# ──────────────────────────────────────────────────────────────────────
# Diálogo principal
# ──────────────────────────────────────────────────────────────────────


class GeoPipeDialog(QDialog):
    """Janela principal do plugin GeoPipe."""

    def __init__(self, parent=None, iface=None):
        super().__init__(parent)
        self.iface = iface
        self._task: PipelineTask | None = None
        self._read_task: QgsTask | None = None
        self._step_checks: list[QCheckBox] = []
        self._last_log_path = ""
        self._current_step_idx = 0

        self.setWindowTitle("GeoPipe – Pipeline Geoespacial")
        self.setMinimumSize(920, 900)
        self.resize(960, 960)
        self.setWindowFlags(self.windowFlags() | Qt.WindowMaximizeButtonHint)

        self._build_ui()
        self._on_mode_changed()
        self._populate_rasters()
        self._populate_vectors()
        self._check_dependencies()
        self._load_settings()

    # ------------------------------------------------------------------
    # Construção da UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(10, 10, 10, 10)
        root_layout.setSpacing(6)

        # Scroll area para acomodar resolucoes menores
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        container = QWidget()
        main = QVBoxLayout(container)
        main.setSpacing(6)
        scroll.setWidget(container)
        root_layout.addWidget(scroll, stretch=1)

        # ── Rodapé ────────────────────────────────────────────────────────
        sep_footer = QFrame()
        sep_footer.setFrameShape(QFrame.HLine)
        sep_footer.setFrameShadow(QFrame.Sunken)
        root_layout.addWidget(sep_footer)

        footer_lbl = QLabel()
        footer_lbl.setAlignment(Qt.AlignCenter)
        footer_lbl.setStyleSheet("color: gray; font-size: 10px;")
        try:
            import configparser
            _meta = configparser.ConfigParser()
            _meta.read(str(_PLUGIN_DIR / "metadata.txt"), encoding="utf-8")
            _name    = _meta.get("general", "name",    fallback="GeoPipe")
            _version = _meta.get("general", "version", fallback="")
            _author  = _meta.get("general", "author",  fallback="")
            _email   = _meta.get("general", "email",   fallback="")
            _parts = [f"{_name} v{_version}" if _version else _name]
            if _author and _email:
                _parts.append(f"{_author} <{_email}>")
            elif _author:
                _parts.append(_author)
            footer_lbl.setText("  |  ".join(_parts))
        except Exception:
            footer_lbl.setText("GeoPipe")
        root_layout.addWidget(footer_lbl)

        # ── Arquivos e Pastas ──────────────────────────────────────────
        grp_files = QGroupBox("Arquivos e Pastas")
        lay_files = QVBoxLayout(grp_files)
        lay_files.setSpacing(4)

        # ── Raster de entrada: ComboBox editável (camadas abertas ou arquivo)
        row_raster = QHBoxLayout()
        lbl_raster = QLabel("Raster de entrada (.tif):")
        lbl_raster.setMinimumWidth(280)
        row_raster.addWidget(lbl_raster)
        self._raster_cmb = QComboBox()
        self._raster_cmb.setEditable(True)
        self._raster_cmb.setInsertPolicy(QComboBox.NoInsert)
        self._raster_cmb.lineEdit().setPlaceholderText("Selecione uma camada aberta ou procure o arquivo (.tif)")
        self._raster_cmb.setToolTip(
            "Raster classificado de entrada (.tif).\n"
            "Selecione uma camada já aberta no QGIS ou\n"
            "informe o caminho completo do arquivo no disco."
        )
        row_raster.addWidget(self._raster_cmb)
        btn_raster_refresh = QPushButton("↺")
        btn_raster_refresh.setFixedWidth(28)
        btn_raster_refresh.setToolTip("Atualizar lista de camadas raster abertas no QGIS")
        btn_raster_refresh.clicked.connect(self._populate_rasters)
        row_raster.addWidget(btn_raster_refresh)
        btn_raster_browse = QPushButton("Procurar…")
        btn_raster_browse.setFixedWidth(85)
        def _browse_raster():
            path, _ = QFileDialog.getOpenFileName(self, "Raster de entrada",
                                                  filter="GeoTIFF (*.tif *.tiff);;Todos (*.*)")
            if path:
                # Adiciona como item avulso e seleciona
                self._raster_cmb.insertItem(0, path, path)
                self._raster_cmb.setCurrentIndex(0)
        btn_raster_browse.clicked.connect(_browse_raster)
        row_raster.addWidget(btn_raster_browse)
        lay_files.addLayout(row_raster)
        # ── Grade de articulação: ComboBox editável (camadas vetoriais abertas ou arquivo)
        row_grid = QHBoxLayout()
        lbl_grid = QLabel("Grade de articulação (.shp):")
        lbl_grid.setMinimumWidth(280)
        row_grid.addWidget(lbl_grid)
        self._grid_cmb = QComboBox()
        self._grid_cmb.setEditable(True)
        self._grid_cmb.setInsertPolicy(QComboBox.NoInsert)
        self._grid_cmb.lineEdit().setPlaceholderText("Selecione uma camada aberta ou procure o arquivo (.shp)")
        self._grid_cmb.setToolTip(
            "Grade de articulação (.shp).\n"
            "Define os tiles que serão processados em paralelo.\n"
            "Selecione uma camada aberta no QGIS ou informe o caminho do arquivo."
        )
        row_grid.addWidget(self._grid_cmb)
        btn_grid_refresh = QPushButton("↺")
        btn_grid_refresh.setFixedWidth(28)
        btn_grid_refresh.setToolTip("Atualizar lista de camadas vetoriais abertas no QGIS")
        btn_grid_refresh.clicked.connect(self._populate_vectors)
        row_grid.addWidget(btn_grid_refresh)
        btn_grid_browse = QPushButton("Procurar…")
        btn_grid_browse.setFixedWidth(85)
        def _browse_grid():
            path, _ = QFileDialog.getOpenFileName(self, "Grade de articulação",
                                                  filter="Shapefile (*.shp);;Todos (*.*)")
            if path:
                self._grid_cmb.insertItem(0, path, path)
                self._grid_cmb.setCurrentIndex(0)
        btn_grid_browse.clicked.connect(_browse_grid)
        row_grid.addWidget(btn_grid_browse)
        lay_files.addLayout(row_grid)
        self._tiles_edit, _ = self._add_path_row(lay_files, "Pasta de tiles (saída #1 / entrada #2):", mode="dir",
                                                  placeholder="Pasta onde os tiles serão salvos")
        self._tiles_edit.setToolTip(
            "Pasta onde os shapefiles de tiles são salvos (saída da etapa 1)\n"
            "e de onde são lidos para o merge (entrada da etapa 2).\n"
            "Deve ter espaço suficiente para todos os tiles gerados."
        )
        self._output_edit, _ = self._add_path_row(lay_files, "Pasta de entrega final (saída #2–#4):", mode="dir",
                                                   placeholder="Pasta para shapefiles finais (merge, índice, zip)")
        self._output_edit.setToolTip(
            "Pasta de entrega final: recebe o shapefile mergeado (etapa 2),\n"
            "o índice espacial .qix (etapa 3) e os ZIPs por classe (etapa 4)."
        )
        self._upload_edit, _ = self._add_path_row(lay_files, "Pasta para upload (saída #5):", mode="dir",
                                                   placeholder="Pasta de destino com pastas numeradas (shape.zip)")
        self._upload_edit.setToolTip(
            "Pasta de upload: recebe subpastas numeradas com shape.zip (etapa 5).\n"
            "Estrutura gerada: upload_dir/001/shape.zip, 002/shape.zip, etc."
        )
        main.addWidget(grp_files)

        # ── Tipo de Pipeline ───────────────────────────────────────────
        grp_mode = QGroupBox("Tipo de Pipeline")
        lay_mode = QVBoxLayout(grp_mode)

        self._rb_padrao = QRadioButton("Uso do Solo (Classes – Adaptação BB Valoração)")
        self._rb_padrao.setChecked(True)
        self._rb_decl = QRadioButton("Declividade (Campos FAIXA e NOME customizados)")
        self._rb_din = QRadioButton("Livre/Personalizado (Definição Dinâmica de Colunas)")

        for rb in (self._rb_padrao, self._rb_decl, self._rb_din):
            lay_mode.addWidget(rb)
            rb.toggled.connect(self._on_mode_changed)

        self._rb_padrao.setToolTip(
            "Vetorização de uso do solo com classes DN.\n"
            "Preenche atributos padrão da tabela BB Valoração\n"
            "(agricultura, pastagem, silvicultura, etc.)."
        )
        self._rb_decl.setToolTip(
            "Vetorização de dados de declividade.\n"
            "Preenche automaticamente os campos FAIXA e NOME\n"
            "conforme a classe DN selecionada."
        )
        self._rb_din.setToolTip(
            "Modo livre: defina suas próprias colunas, tipos, tamanhos\n"
            "e valores constantes. Indicado para dados fora dos\n"
            "padrões BB Valoração ou declividade."
        )

        main.addWidget(grp_mode)

        # ── Vetorização ────────────────────────────────────────────────
        grp_vec = QGroupBox("Vetorização")
        lay_vec_v = QVBoxLayout(grp_vec)
        row1 = QHBoxLayout()
        row2 = QHBoxLayout()

        row1.addWidget(QLabel("DN (classe):"))
        self._cmb_dn = QComboBox()
        self._cmb_dn.setMinimumWidth(280)
        self._cmb_dn.setToolTip(
            "Valor DN (Digital Number) da classe alvo a extrair do raster.\n"
            "• Modo Padrão: cada DN representa um tipo de uso do solo.\n"
            "• Modo Declividade: cada DN representa uma faixa de inclinação."
        )
        row1.addWidget(self._cmb_dn)

        self._btn_ler_raster = QPushButton("Ler Raster")
        self._btn_ler_raster.setFixedWidth(90)
        self._btn_ler_raster.clicked.connect(self._load_raster_classes_bg)
        self._btn_ler_raster.setVisible(False)
        row1.addWidget(self._btn_ler_raster)

        row1.addSpacerItem(QSpacerItem(16, 0, QSizePolicy.Fixed))
        row1.addWidget(QLabel("Tolerância D-P:"))
        self._tol_edit = QLineEdit("0.0001")
        self._tol_edit.setFixedWidth(80)
        self._tol_edit.setToolTip(
            "Tolerância Douglas-Peucker para simplificação dos polígonos (em graus).\n"
            "• 0.0001 ≈ 11 m no equador — padrão, bom equilíbrio detalhe/tamanho\n"
            "• Valores menores → mais vértices, arquivos maiores\n"
            "• Valores maiores → contornos mais suavizados, arquivos menores"
        )
        row1.addWidget(self._tol_edit)
        row1.addStretch()

        row2.addWidget(QLabel("Max Workers:"))
        default_workers = str(max(1, (os.cpu_count() or 4) - 2))
        self._workers_edit = QLineEdit(default_workers)
        self._workers_edit.setFixedWidth(60)
        self._workers_edit.setToolTip(
            "Número de processos paralelos usados na vetorização.\n"
            "• Valores maiores aceleram o processo, mas consomem mais CPU e RAM.\n"
            "• Recomendado: número de núcleos físicos do processador (ex.: 8, 14, 16).\n"
            "• Evite ultrapassar o total de threads disponíveis no hardware."
        )
        row2.addWidget(self._workers_edit)
        row2.addStretch()

        lay_vec_v.addLayout(row1)
        lay_vec_v.addLayout(row2)
        main.addWidget(grp_vec)

        # ── Merge / Tabela de Atributos ────────────────────────────────
        grp_merge = QGroupBox("Merge / Tabela de Atributos")
        self._lay_merge = QVBoxLayout(grp_merge)
        self._lay_merge.setSpacing(4)

        # Linha 1: Meta tamanho + UF
        row_merge1 = QHBoxLayout()
        row_merge1.addWidget(QLabel("Meta tamanho (MB):"))
        self._mb_edit = QLineEdit("300")
        self._mb_edit.setFixedWidth(60)
        self._mb_edit.setToolTip(
            "Tamanho-alvo máximo por arquivo shapefile de saída (em MB).\n"
            "O merge dividirá os tiles em múltiplos shapefiles\n"
            "caso o volume total ultrapasse esse limite."
        )
        row_merge1.addWidget(self._mb_edit)
        row_merge1.addSpacerItem(QSpacerItem(16, 0, QSizePolicy.Fixed))
        self._lbl_uf = QLabel("UF:")
        row_merge1.addWidget(self._lbl_uf)
        self._uf_edit = QLineEdit("BR")
        self._uf_edit.setFixedWidth(50)
        row_merge1.addWidget(self._uf_edit)
        row_merge1.addStretch()
        self._lay_merge.addLayout(row_merge1)

        # Linha 2: Descrição
        row_merge2 = QHBoxLayout()
        self._lbl_descrico = QLabel("Descrição:")
        row_merge2.addWidget(self._lbl_descrico)
        self._desc_edit = QLineEdit()
        self._desc_edit.setReadOnly(True)
        row_merge2.addWidget(self._desc_edit)
        self._lay_merge.addLayout(row_merge2)

        # Painel FAIXA / NOME (Declividade) — inicialmente oculto
        self._wgt_custom = QWidget()
        lay_custom = QVBoxLayout(self._wgt_custom)
        lay_custom.setContentsMargins(0, 0, 0, 0)
        lay_custom.setSpacing(4)

        row_faixa = QHBoxLayout()
        row_faixa.addWidget(QLabel("Faixa:"))
        self._faixa_edit = QLineEdit()
        row_faixa.addWidget(self._faixa_edit)
        lay_custom.addLayout(row_faixa)

        row_nome = QHBoxLayout()
        row_nome.addWidget(QLabel("Nome:"))
        self._nome_edit = QLineEdit()
        row_nome.addWidget(self._nome_edit)
        lay_custom.addLayout(row_nome)

        self._lay_merge.addWidget(self._wgt_custom)
        self._wgt_custom.setVisible(False)

        # Painel Dinâmico — inicialmente oculto
        self._wgt_dinamico = QWidget()
        lay_din = QVBoxLayout(self._wgt_dinamico)
        lay_din.setContentsMargins(0, 0, 0, 0)
        lay_din.setSpacing(4)

        self._tbl_dynamic = QTableWidget(0, 4)
        self._tbl_dynamic.setHorizontalHeaderLabels(["Nome da Coluna", "Tipo", "Tamanho/Precisão", "Valor Constante"])
        self._tbl_dynamic.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._tbl_dynamic.setFixedHeight(110)
        lay_din.addWidget(self._tbl_dynamic)

        # Controles de adição/remoção
        row_din_ctrl = QHBoxLayout()
        self._dyn_nome_edit = QLineEdit()
        self._dyn_nome_edit.setPlaceholderText("Nome")
        self._dyn_nome_edit.setFixedWidth(120)
        row_din_ctrl.addWidget(self._dyn_nome_edit)

        self._dyn_tipo_cmb = QComboBox()
        self._dyn_tipo_cmb.addItems(["str", "int", "float"])
        self._dyn_tipo_cmb.setFixedWidth(60)
        row_din_ctrl.addWidget(self._dyn_tipo_cmb)

        self._dyn_tam_edit = QLineEdit("254")
        self._dyn_tam_edit.setFixedWidth(50)
        row_din_ctrl.addWidget(self._dyn_tam_edit)

        self._dyn_val_edit = QLineEdit()
        self._dyn_val_edit.setPlaceholderText("Valor")
        row_din_ctrl.addWidget(self._dyn_val_edit)

        btn_add_col = QPushButton("+ Adicionar")
        btn_add_col.clicked.connect(self._add_dynamic_column)
        row_din_ctrl.addWidget(btn_add_col)

        btn_rem_col = QPushButton("− Remover")
        btn_rem_col.clicked.connect(self._remove_dynamic_column)
        row_din_ctrl.addWidget(btn_rem_col)

        lay_din.addLayout(row_din_ctrl)
        self._lay_merge.addWidget(self._wgt_dinamico)
        self._wgt_dinamico.setVisible(False)

        main.addWidget(grp_merge)

        # ── Etapas a Executar ──────────────────────────────────────────
        self._grp_steps = QGroupBox("Etapas a Executar")
        self._lay_steps = QVBoxLayout(self._grp_steps)
        self._lay_steps.setSpacing(4)

        self._wgt_checks = QWidget()
        from qgis.PyQt.QtWidgets import QGridLayout
        self._grid_checks = QGridLayout(self._wgt_checks)
        self._grid_checks.setSpacing(4)
        self._lay_steps.addWidget(self._wgt_checks)

        # Separador + cleanup
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        self._lay_steps.addWidget(sep)

        self._chk_cleanup = QCheckBox("Limpar tiles intermediários após conclusão")
        self._chk_cleanup.setChecked(True)
        self._lay_steps.addWidget(self._chk_cleanup)

        self._chk_keep_shp = QCheckBox("Preservar shapefiles descompactados após compactação (.zip)")
        self._chk_keep_shp.setChecked(False)
        self._lay_steps.addWidget(self._chk_keep_shp)

        # Botões de seleção + Executar/Cancelar
        row_btns = QHBoxLayout()
        btn_sel_all = QPushButton("Selecionar Todas")
        btn_sel_all.clicked.connect(self._select_all_steps)
        row_btns.addWidget(btn_sel_all)

        btn_desel_all = QPushButton("Desmarcar Todas")
        btn_desel_all.clicked.connect(self._deselect_all_steps)
        row_btns.addWidget(btn_desel_all)

        row_btns.addSpacerItem(QSpacerItem(16, 0, QSizePolicy.Fixed))

        self._btn_run = QPushButton("▶  Executar")
        self._btn_run.setStyleSheet("font-weight: bold;")
        self._btn_run.clicked.connect(self._on_run)
        row_btns.addWidget(self._btn_run)

        self._btn_cancel = QPushButton("⏹  Cancelar")
        self._btn_cancel.setEnabled(False)
        self._btn_cancel.clicked.connect(self._on_cancel)
        row_btns.addWidget(self._btn_cancel)

        row_btns.addStretch()
        self._lay_steps.addLayout(row_btns)

        main.addWidget(self._grp_steps)

        # ── Barra de Progresso ─────────────────────────────────────────
        self._progress = QProgressBar()
        self._progress.setTextVisible(True)
        self._progress.setFormat("Pronto")
        self._progress.setValue(0)
        main.addWidget(self._progress)

        # ── Log ────────────────────────────────────────────────────────
        grp_log = QGroupBox("Log")
        lay_log = QVBoxLayout(grp_log)
        self._txt_log = QPlainTextEdit()
        self._txt_log.setReadOnly(True)
        self._txt_log.setFont(QFont("Consolas", 9))
        self._txt_log.setStyleSheet("background-color: #1e1e1e; color: #d4d4d4;")
        self._txt_log.setMinimumHeight(200)
        lay_log.addWidget(self._txt_log)

        row_log_btns = QHBoxLayout()
        self._btn_open_log = QPushButton("Abrir Log")
        self._btn_open_log.setEnabled(False)
        self._btn_open_log.setToolTip("Abre o arquivo de log da última execução no editor padrão")
        self._btn_open_log.clicked.connect(self._open_last_log)
        row_log_btns.addWidget(self._btn_open_log)
        row_log_btns.addStretch()
        lay_log.addLayout(row_log_btns)

        main.addWidget(grp_log, stretch=1)

        main.addStretch()

        # Inicializar checkboxes padrão
        self._rebuild_steps_checkboxes(PIPELINE_STEPS)
        # Inicializar combobox DN
        self._update_dn_options(DN_CLASSES)
        self._cmb_dn.currentIndexChanged.connect(self._on_dn_selected)

    # ------------------------------------------------------------------
    # Helpers de layout
    # ------------------------------------------------------------------

    def _get_raster_path(self) -> str:
        """Retorna o caminho físico do raster selecionado no ComboBox."""
        idx = self._raster_cmb.currentIndex()
        data = self._raster_cmb.itemData(idx) if idx >= 0 else None
        if data:
            return str(data)
        return self._raster_cmb.currentText().strip()

    def _get_grid_path(self) -> str:
        """Retorna o caminho físico da grade selecionada no ComboBox."""
        idx = self._grid_cmb.currentIndex()
        data = self._grid_cmb.itemData(idx) if idx >= 0 else None
        if data:
            return str(data)
        return self._grid_cmb.currentText().strip()

    def _populate_rasters(self):
        """Varre camadas raster abertas no projeto QGIS e preenche o ComboBox."""
        current_path = self._get_raster_path()
        self._raster_cmb.blockSignals(True)
        self._raster_cmb.clear()
        for layer in QgsProject.instance().mapLayers().values():
            if layer.type() == QgsMapLayer.RasterLayer:
                source = layer.source().split("|")[0]
                self._raster_cmb.addItem(f"[QGIS] {layer.name()}", userData=source)
        if current_path:
            for i in range(self._raster_cmb.count()):
                if self._raster_cmb.itemData(i) == current_path:
                    self._raster_cmb.setCurrentIndex(i)
                    break
            else:
                self._raster_cmb.setCurrentIndex(-1)
                self._raster_cmb.lineEdit().setText(current_path)
        self._raster_cmb.blockSignals(False)

    def _populate_vectors(self):
        """Varre camadas vetoriais abertas no projeto QGIS e preenche o ComboBox da grade."""
        current_path = self._get_grid_path()
        self._grid_cmb.blockSignals(True)
        self._grid_cmb.clear()
        for layer in QgsProject.instance().mapLayers().values():
            if layer.type() == QgsMapLayer.VectorLayer:
                source = layer.source().split("|")[0]
                self._grid_cmb.addItem(f"[QGIS] {layer.name()}", userData=source)
        if current_path:
            for i in range(self._grid_cmb.count()):
                if self._grid_cmb.itemData(i) == current_path:
                    self._grid_cmb.setCurrentIndex(i)
                    break
            else:
                self._grid_cmb.setCurrentIndex(-1)
                self._grid_cmb.lineEdit().setText(current_path)
        self._grid_cmb.blockSignals(False)

    def _add_path_row(self, parent_layout, label_text, mode="dir", filetypes="", placeholder=""):
        """Cria linha: Label + LineEdit + Botão Procurar. Retorna (QLineEdit, QPushButton)."""
        row = QHBoxLayout()
        lbl = QLabel(label_text)
        lbl.setMinimumWidth(280)
        row.addWidget(lbl)

        edit = QLineEdit()
        if placeholder:
            edit.setPlaceholderText(placeholder)
        row.addWidget(edit)

        btn = QPushButton("Procurar…")
        btn.setFixedWidth(85)

        def _browse():
            if mode == "dir":
                path = QFileDialog.getExistingDirectory(self, label_text)
            else:
                path, _ = QFileDialog.getOpenFileName(self, label_text, filter=filetypes)
            if path:
                edit.setText(path)

        btn.clicked.connect(_browse)
        row.addWidget(btn)
        parent_layout.addLayout(row)
        return edit, btn

    # ------------------------------------------------------------------
    # Eventos de modo
    # ------------------------------------------------------------------

    def _on_mode_changed(self):
        """Ajusta a interface conforme o modo selecionado."""
        if self._rb_padrao.isChecked():
            mode = "padrao"
        elif self._rb_decl.isChecked():
            mode = "declividade"
        else:
            mode = "dinamico"

        # Checkboxes de etapas
        if mode == "padrao":
            self._rebuild_steps_checkboxes(PIPELINE_STEPS)
        elif mode == "declividade":
            self._rebuild_steps_checkboxes(PIPELINE_STEPS_CUSTOM)
        else:
            self._rebuild_steps_checkboxes(PIPELINE_STEPS_DINAMICO)

        # Ocultar painéis condicionais
        self._wgt_custom.setVisible(False)
        self._wgt_dinamico.setVisible(False)

        if mode == "padrao":
            self._show_standard_fields()
            self._btn_ler_raster.setVisible(False)
            self._cmb_dn.setEnabled(True)
            self._update_dn_options(DN_CLASSES)
        elif mode == "declividade":
            self._show_standard_fields()
            self._btn_ler_raster.setVisible(False)
            self._cmb_dn.setEnabled(True)
            self._update_dn_options(DN_CLASSES_DECLIVIDADE)
            self._wgt_custom.setVisible(True)
            self._update_custom_defaults()
        else:  # dinamico
            self._hide_standard_fields()
            self._btn_ler_raster.setVisible(True)
            self._cmb_dn.setEnabled(True)
            self._cmb_dn.clear()
            self._wgt_dinamico.setVisible(True)

    def _show_standard_fields(self):
        self._lbl_uf.setVisible(True)
        self._uf_edit.setVisible(True)
        self._lbl_descrico.setVisible(True)
        self._desc_edit.setVisible(True)

    def _hide_standard_fields(self):
        self._lbl_uf.setVisible(False)
        self._uf_edit.setVisible(False)
        self._lbl_descrico.setVisible(False)
        self._desc_edit.setVisible(False)

    def _rebuild_steps_checkboxes(self, steps_list):
        """Recria os checkboxes de etapas em grid de 3 colunas."""
        # Limpar
        for i in reversed(range(self._grid_checks.count())):
            w = self._grid_checks.itemAt(i).widget()
            if w:
                w.setParent(None)
        self._step_checks.clear()

        for i, (_, desc) in enumerate(steps_list):
            chk = QCheckBox(f"{i + 1} – {desc}")
            chk.setChecked(True)
            self._grid_checks.addWidget(chk, i // 3, i % 3)
            self._step_checks.append(chk)

    def _select_all_steps(self):
        for chk in self._step_checks:
            chk.setChecked(True)

    def _deselect_all_steps(self):
        for chk in self._step_checks:
            chk.setChecked(False)

    # ------------------------------------------------------------------
    # DN / Descrição
    # ------------------------------------------------------------------

    def _update_dn_options(self, dn_map: dict):
        self._cmb_dn.blockSignals(True)
        self._cmb_dn.clear()
        for dn, desc in dn_map.items():
            self._cmb_dn.addItem(f"{dn} - {desc}", userData=dn)
        self._cmb_dn.blockSignals(False)
        self._on_dn_selected()

    def _on_dn_selected(self):
        dn = self._cmb_dn.currentData()
        if dn is None:
            return
        if self._rb_decl.isChecked():
            self._desc_edit.setText(DN_CLASSES_DECLIVIDADE.get(dn, ""))
            self._update_custom_defaults()
        else:
            self._desc_edit.setText(DN_CLASSES.get(dn, ""))

    def _get_selected_dn(self) -> str:
        dn = self._cmb_dn.currentData()
        return str(dn) if dn is not None else ""

    def _update_custom_defaults(self):
        if not self._rb_decl.isChecked():
            return
        dn = self._cmb_dn.currentData()
        if dn and dn in DECLIV_DEFAULTS:
            self._faixa_edit.setText(DECLIV_DEFAULTS[dn][0])
            self._nome_edit.setText(DECLIV_DEFAULTS[dn][1])

    # ------------------------------------------------------------------
    # Colunas dinâmicas
    # ------------------------------------------------------------------

    def _add_dynamic_column(self):
        nome = self._dyn_nome_edit.text().strip()
        if not nome:
            QMessageBox.warning(self, "Aviso", "O nome da coluna é obrigatório.")
            return

        if len(nome) > 10:
            QMessageBox.warning(
                self,
                "Nome de Coluna Inválido",
                f"O nome da coluna '{nome}' possui {len(nome)} caracteres.\n\n"
                "O formato Shapefile (dBASE III) limita nomes de coluna a no máximo 10 caracteres.",
            )
            return

        if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", nome):
            QMessageBox.warning(
                self,
                "Nome de Coluna Inválido",
                f"O nome da coluna '{nome}' contém caracteres inválidos.\n\n"
                "Utilize apenas letras, números e sublinhados (_), iniciando com letra ou sublinhado.",
            )
            return
        tipo = self._dyn_tipo_cmb.currentText()
        tam = self._dyn_tam_edit.text().strip()
        val = self._dyn_val_edit.text().strip()

        # Verificar duplicidade
        for row in range(self._tbl_dynamic.rowCount()):
            if self._tbl_dynamic.item(row, 0) and self._tbl_dynamic.item(row, 0).text() == nome:
                QMessageBox.warning(self, "Aviso", f"A coluna '{nome}' já foi adicionada.")
                return

        r = self._tbl_dynamic.rowCount()
        self._tbl_dynamic.insertRow(r)
        self._tbl_dynamic.setItem(r, 0, QTableWidgetItem(nome))
        self._tbl_dynamic.setItem(r, 1, QTableWidgetItem(tipo))
        self._tbl_dynamic.setItem(r, 2, QTableWidgetItem(tam))
        self._tbl_dynamic.setItem(r, 3, QTableWidgetItem(val))

        self._dyn_nome_edit.clear()
        self._dyn_val_edit.clear()

    def _remove_dynamic_column(self):
        selected = self._tbl_dynamic.selectedItems()
        if not selected:
            return
        rows = sorted({item.row() for item in selected}, reverse=True)
        for row in rows:
            self._tbl_dynamic.removeRow(row)

    # ------------------------------------------------------------------
    # Log
    # ------------------------------------------------------------------

    def _log(self, msg: str):
        """Insere texto no QPlainTextEdit de log (safe para chamar da main thread)."""
        self._txt_log.appendPlainText(msg)
        self._txt_log.moveCursor(QTextCursor.End)
        QgsMessageLog.logMessage(msg, "GeoPipe", Qgis.Info)

    def _log_from_task(self, msg: str):
        """Slot conectado ao sinal da task — sempre chamado na main thread."""
        self._log(msg)

    # ------------------------------------------------------------------
    # Validação
    # ------------------------------------------------------------------

    def _validate(self) -> bool:
        if not self._cmb_dn.currentText() and not self._rb_din.isChecked():
            QMessageBox.critical(self, "Erro", "Selecione uma classe DN.")
            return False

        if self._rb_din.isChecked() and self._tbl_dynamic.rowCount() == 0:
            QMessageBox.critical(self, "Erro", "Adicione pelo menos uma coluna na tabela.")
            return False

        try:
            float(self._tol_edit.text())
        except ValueError:
            QMessageBox.critical(self, "Erro", "Tolerância Douglas-Peucker deve ser um número.")
            return False

        try:
            w = int(self._workers_edit.text())
            if w < 1:
                raise ValueError
        except ValueError:
            QMessageBox.critical(self, "Erro", "Max Workers deve ser um inteiro ≥ 1.")
            return False

        try:
            mb = int(self._mb_edit.text())
            if mb < 1:
                raise ValueError
        except ValueError:
            QMessageBox.critical(self, "Erro", "Meta de tamanho deve ser um inteiro ≥ 1 MB.")
            return False

        # ── Validação de paths ─────────────────────────────────────────
        # Índices das etapas selecionadas (0-based)
        sel_idx = {i for i, chk in enumerate(self._step_checks) if chk.isChecked()}

        # Raster de entrada (obrigatório para etapa 1)
        if 0 in sel_idx:
            raster = self._get_raster_path()
            if not raster:
                QMessageBox.critical(self, "Erro", "Informe o raster de entrada (.tif).")
                return False
            if not Path(raster).is_file():
                QMessageBox.critical(self, "Erro", f"Raster não encontrado:\n{raster}")
                return False

            # Grade de articulação (obrigatória para etapa 1)
            grid = self._get_grid_path()
            if not grid:
                QMessageBox.critical(self, "Erro", "Informe a grade de articulação (.shp).")
                return False
            if not Path(grid).is_file():
                QMessageBox.critical(self, "Erro", f"Grade de articulação não encontrada:\n{grid}")
                return False

        # Pasta de tiles (obrigatória para etapas 1 ou 2)
        if sel_idx & {0, 1}:
            tiles = self._tiles_edit.text().strip()
            if not tiles:
                QMessageBox.critical(self, "Erro", "Informe a pasta de tiles.")
                return False
            if not Path(tiles).exists():
                r = QMessageBox.question(
                    self, "Criar pasta?",
                    f"A pasta de tiles não existe:\n{tiles}\n\nDeseja criá-la agora?",
                    QMessageBox.Yes | QMessageBox.No,
                )
                if r == QMessageBox.Yes:
                    Path(tiles).mkdir(parents=True, exist_ok=True)
                else:
                    return False

        # Pasta de saída (obrigatória para etapas 2-4)
        if sel_idx & {1, 2, 3}:
            output = self._output_edit.text().strip()
            if not output:
                QMessageBox.critical(self, "Erro", "Informe a pasta de entrega final.")
                return False
            if not Path(output).exists():
                r = QMessageBox.question(
                    self, "Criar pasta?",
                    f"A pasta de saída não existe:\n{output}\n\nDeseja criá-la agora?",
                    QMessageBox.Yes | QMessageBox.No,
                )
                if r == QMessageBox.Yes:
                    Path(output).mkdir(parents=True, exist_ok=True)
                else:
                    return False

        # Pasta de upload (obrigatória para etapa 5)
        if 4 in sel_idx:
            upload = self._upload_edit.text().strip()
            if not upload:
                QMessageBox.critical(self, "Erro", "Informe a pasta para upload.")
                return False
            if not Path(upload).exists():
                r = QMessageBox.question(
                    self, "Criar pasta?",
                    f"A pasta de upload não existe:\n{upload}\n\nDeseja criá-la agora?",
                    QMessageBox.Yes | QMessageBox.No,
                )
                if r == QMessageBox.Yes:
                    Path(upload).mkdir(parents=True, exist_ok=True)
                else:
                    return False

        return True

    # ------------------------------------------------------------------
    # Construção do ambiente de variáveis
    # ------------------------------------------------------------------

    def _build_env(self) -> dict:
        env = os.environ.copy()

        # Determinar raiz OSGeo4W e prefixo Python
        prefix = Path(sys.exec_prefix)
        osgeo4w_root = prefix.parent.parent

        # ── 1. Diretórios de dados Geoespaciais (PROJ / GDAL) ───────────────
        proj_candidates = [
            osgeo4w_root / "share" / "proj",
            prefix / "Library" / "share" / "proj",
            prefix / "share" / "proj",
        ]
        for p_cand in proj_candidates:
            if (p_cand / "proj.db").exists():
                env["PROJ_DATA"] = str(p_cand)
                env["PROJ_LIB"] = str(p_cand)
                break

        gdal_candidates = [
            osgeo4w_root / "apps" / "gdal" / "share" / "gdal",
            prefix / "Library" / "share" / "gdal",
            prefix / "share" / "gdal",
        ]
        for g_cand in gdal_candidates:
            if g_cand.is_dir():
                env["GDAL_DATA"] = str(g_cand)
                break

        gdal_plugin_candidates = [
            osgeo4w_root / "apps" / "gdal" / "lib" / "gdalplugins",
            prefix / "Library" / "lib" / "gdalplugins",
        ]
        for gp_cand in gdal_plugin_candidates:
            if gp_cand.is_dir():
                env["GDAL_DRIVER_PATH"] = str(gp_cand)
                break

        # ── 2. PYTHONHOME e Encoding ─────────────────────────────────────────
        env["PYTHONHOME"] = str(prefix)
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"

        # ── 3. Higienização e Priorização do PATH (Windows) ──────────────────
        if sys.platform == "win32":
            priority_dirs = [
                str(osgeo4w_root / "bin"),
                str(prefix / "Scripts"),
                str(prefix),
                str(osgeo4w_root / "apps" / "qgis-ltr" / "bin"),
            ]
            existing_priority = [d for d in priority_dirs if Path(d).is_dir()]

            is_osgeo4w = (osgeo4w_root / "bin" / "proj_9.dll").exists() or (
                osgeo4w_root / "bin" / "o4w_env.bat"
            ).exists()

            raw_path = env.get("PATH", "").split(os.pathsep)
            clean_paths: list[str] = []
            for item in raw_path:
                item_strip = item.strip()
                if not item_strip:
                    continue
                item_lower = item_strip.lower()
                # Remove entradas de Conda/Anaconda para evitar sequestro de DLLs (ex: proj_9.dll)
                if is_osgeo4w and ("miniconda" in item_lower or "anaconda" in item_lower):
                    continue
                if item_strip not in existing_priority:
                    clean_paths.append(item_strip)

            env["PATH"] = os.pathsep.join(existing_priority + clean_paths)

        # ── 4. Variáveis operacionais do GeoPipe ─────────────────────────────
        env["PIPE_RASTER_PATH"] = self._get_raster_path()
        env["PIPE_GRID_PATH"] = self._get_grid_path()
        env["PIPE_TILES_DIR"] = self._tiles_edit.text()
        env["PIPE_OUTPUT_DIR"] = self._output_edit.text()
        env["PIPE_TARGET_CLASS"] = self._get_selected_dn()
        env["PIPE_SIMPLIFY_TOL"] = self._tol_edit.text()
        env["PIPE_MAX_WORKERS"] = self._workers_edit.text()
        env["PIPE_TARGET_SIZE_MB"] = self._mb_edit.text()
        env["PIPE_DESCRICAO"] = self._desc_edit.text()
        env["PIPE_UF"] = self._uf_edit.text()
        env["PIPE_UPLOAD_DIR"] = self._upload_edit.text()
        env["PIPE_ATTR_FAIXA"] = self._faixa_edit.text()
        env["PIPE_ATTR_NOME"] = self._nome_edit.text()
        env["PIPE_KEEP_UNZIPPED"] = "1" if self._chk_keep_shp.isChecked() else "0"

        if self._rb_din.isChecked():
            dynamic_cols = []
            for row in range(self._tbl_dynamic.rowCount()):
                dynamic_cols.append({
                    "nome": self._tbl_dynamic.item(row, 0).text() if self._tbl_dynamic.item(row, 0) else "",
                    "tipo": self._tbl_dynamic.item(row, 1).text() if self._tbl_dynamic.item(row, 1) else "str",
                    "tamanho": self._tbl_dynamic.item(row, 2).text() if self._tbl_dynamic.item(row, 2) else "254",
                    "valor": self._tbl_dynamic.item(row, 3).text() if self._tbl_dynamic.item(row, 3) else "",
                })
            env["PIPE_CUSTOM_SCHEMA"] = json.dumps(dynamic_cols)

        return env


    # ------------------------------------------------------------------
    # Execução
    # ------------------------------------------------------------------

    def _on_run(self):
        if not self._validate():
            return

        if not any(chk.isChecked() for chk in self._step_checks):
            QMessageBox.warning(self, "Nenhuma Etapa", "Selecione ao menos uma etapa para executar.")
            return

        # ── Validação de tiles vazios ──────────────────────────────────
        _sel_idx = {i for i, chk in enumerate(self._step_checks) if chk.isChecked()}
        if 1 in _sel_idx and 0 not in _sel_idx:
            _tiles = self._tiles_edit.text().strip()
            if _tiles and Path(_tiles).exists() and not list(Path(_tiles).glob("*.shp")):
                if QMessageBox.question(
                    self, "Pasta de Tiles Vazia",
                    f"A pasta de tiles não contém arquivos .shp:\n{_tiles}\n\n"
                    "A etapa 2 (Merge) depende dos tiles gerados pela etapa 1.\n\n"
                    "Deseja continuar mesmo assim?",
                    QMessageBox.Yes | QMessageBox.No,
                ) == QMessageBox.No:
                    return

        self._txt_log.clear()

        # Determinar lista de steps do modo atual
        if self._rb_padrao.isChecked():
            current_steps = PIPELINE_STEPS
        elif self._rb_decl.isChecked():
            current_steps = PIPELINE_STEPS_CUSTOM
        else:
            current_steps = PIPELINE_STEPS_DINAMICO

        selected = [
            (i + 1, script, desc)
            for i, ((script, desc), chk) in enumerate(zip(current_steps, self._step_checks))
            if chk.isChecked()
        ]

        # ── Confirmação antes de executar ─────────────────────────────
        if self._rb_padrao.isChecked():
            _mode_str = "Uso do Solo"
        elif self._rb_decl.isChecked():
            _mode_str = "Declividade"
        else:
            _mode_str = "Livre/Personalizado"
        _steps_nums = ", ".join(str(s[0]) for s in selected)
        _dn_str = self._cmb_dn.currentText() or "(nenhum)"
        _raster_str = self._get_raster_path() or "(não definido)"
        _confirm_msg = (
            f"Modo: {_mode_str}\n"
            f"Classe DN: {_dn_str}\n"
            f"Tolerância D-P: {self._tol_edit.text()}  |  Workers: {self._workers_edit.text()}\n"
            f"Etapas: {_steps_nums}\n"
            f"Raster: {_raster_str}\n"
            "\nConfirmar execução?"
        )
        if QMessageBox.question(
            self, "Confirmar Pipeline", _confirm_msg,
            QMessageBox.Yes | QMessageBox.Cancel,
        ) != QMessageBox.Yes:
            return

        num_selected = len(selected)
        self._progress.setMaximum(num_selected * 100)
        self._progress.setValue(0)
        self._progress.setFormat("Iniciando…")
        self._current_step_idx = 0

        self._set_running(True)

        env = self._build_env()
        cleanup = self._chk_cleanup.isChecked()
        tiles_dir = self._tiles_edit.text()

        # Log: tenta pasta de upload, depois pasta de saída ("prontos"). Se ambas vazias, pergunta ao usuário.
        upload_dir = self._upload_edit.text().strip()
        output_dir = self._output_edit.text().strip()
        
        if upload_dir:
            log_dir = upload_dir
        elif output_dir:
            log_dir = output_dir
        else:
            log_dir = QFileDialog.getExistingDirectory(self, "Selecione a pasta para salvar o log de execução")
            if not log_dir:
                self._set_running(False)
                self._txt_log.appendPlainText("⚠️  Execução cancelada: pasta de log não informada.")
                return

        self._task = PipelineTask(selected, env, cleanup, tiles_dir, log_dir)
        self._task.log_message.connect(self._log_from_task)
        self._task.progress_update.connect(self._on_progress_update)
        self._task.finished_signal.connect(self._on_pipeline_finished)
        self._task.log_path_signal.connect(self._on_log_path_set)
        self._task.step_tile_progress.connect(self._on_tile_progress)

        QgsApplication.taskManager().addTask(self._task)

    def _on_cancel(self):
        if _is_task_running(self._task):
            try:
                self._task.cancel()
            except (RuntimeError, ReferenceError):
                pass
        self._log("⚠️  Cancelamento solicitado pelo usuário.")

    def _on_progress_update(self, value: int, label: str):
        self._current_step_idx = value
        self._progress.setValue(value * 100)
        self._progress.setFormat(label)

    def _on_pipeline_finished(self, success: bool):
        self._set_running(False)
        self._task = None
        if success:
            if self.iface:
                self.iface.messageBar().pushMessage("GeoPipe", "Pipeline concluído com sucesso! 🎉", level=Qgis.Success, duration=5)
            self._load_result_layer()
        else:
            if self.iface:
                self.iface.messageBar().pushMessage("GeoPipe", "Pipeline encerrado com erros ou cancelado.", level=Qgis.Warning, duration=5)

    def _load_result_layer(self):
        """Carrega o shapefile resultante no QGIS após o pipeline.

        Estratégia:
        1. Procura .shp avulso na pasta de saída (caso a etapa 5 não tenha
           sido executada e os arquivos ainda existam descompactados).
        2. Se não encontrar, busca shape.zip nas subpastas numeradas da pasta
           de upload e carrega via /vsizip/ (sem extrair nada).
        """
        if not self.iface:
            return

        # Só carrega se a etapa de merge (índice 1 = etapa 2) foi executada
        if len(self._step_checks) <= 1 or not self._step_checks[1].isChecked():
            return

        # ── Tentativa 1: .shp avulso na pasta de saída ─────────────────
        output_dir = self._output_edit.text().strip()
        if output_dir and Path(output_dir).exists():
            shps = sorted(
                Path(output_dir).glob("*.shp"),
                key=lambda f: f.stat().st_mtime,
                reverse=True,
            )
            if shps:
                self._add_vector_layer(str(shps[0]), shps[0].stem)
                return

        # ── Tentativa 2: .zip em Zips_Prontos_DN_X (resultado da etapa 4) ─
        dn = self._get_selected_dn()
        if output_dir and dn:
            zips_dir = Path(output_dir) / f"Zips_Prontos_DN_{dn}"
            if zips_dir.exists():
                zips = sorted(
                    zips_dir.glob("*.zip"),
                    key=lambda f: f.stat().st_mtime,
                    reverse=True,
                )
                if zips:
                    newest = zips[0]
                    # Arquivos gravados flat no zip (arcname=file.name)
                    vsizip_path = f"/vsizip/{newest}/{newest.stem}.shp"
                    self._add_vector_layer(vsizip_path, newest.stem)
                    return

        # ── Tentativa 3: shape.zip nas subpastas numeradas do upload (etapa 5) ─
        upload_dir = self._upload_edit.text().strip()
        if not upload_dir or not Path(upload_dir).exists():
            self._log("ℹ️  Nenhuma camada encontrada para carregar no QGIS após o pipeline.")
            return

        # Coleta todos shape.zip em subpastas de um nível (1/, 2/, …)
        shape_zips = sorted(
            Path(upload_dir).glob("*/shape.zip"),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )
        if not shape_zips:
            self._log("ℹ️  Nenhum shape.zip encontrado na pasta de upload para carregar.")
            return

        newest_zip = shape_zips[0]
        # Caminho /vsizip/ — QGIS/GDAL lê o .shp de dentro do zip sem extrair
        vsizip_path = f"/vsizip/{newest_zip}/shape/shape.shp"
        layer_name = f"shape [{newest_zip.parent.name}]"
        self._add_vector_layer(vsizip_path, layer_name)

    def _add_vector_layer(self, path: str, name: str) -> None:
        """Adiciona uma camada vetorial ao QGIS e exibe notificação."""
        layer = self.iface.addVectorLayer(path, name, "ogr")
        if layer and layer.isValid():
            self.iface.messageBar().pushMessage(
                "GeoPipe",
                f"Camada '{name}' adicionada ao mapa.",
                level=Qgis.Info,
                duration=6,
            )
            self._log(f"✅ Camada carregada no QGIS: {name}")
        else:
            self._log(f"⚠️  Não foi possível carregar a camada: {name} ({path})")

    def _set_running(self, running: bool):
        self._btn_run.setEnabled(not running)
        self._btn_cancel.setEnabled(running)
        self._chk_cleanup.setEnabled(not running)
        self._chk_keep_shp.setEnabled(not running)
        for chk in self._step_checks:
            chk.setEnabled(not running)

    # ------------------------------------------------------------------
    # Persistência de configurações
    # ------------------------------------------------------------------

    def closeEvent(self, event):
        if _is_task_running(self._task):
            r = QMessageBox.question(
                self,
                "Pipeline em execução",
                "Existe um processamento do pipeline em andamento.\n\n"
                "Deseja realmente cancelar a execução e fechar a janela?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if r != QMessageBox.Yes:
                event.ignore()
                return
            try:
                self._task.cancel()
            except (RuntimeError, ReferenceError):
                pass

        if _is_task_running(self._read_task):
            try:
                self._read_task.cancel()
            except (RuntimeError, ReferenceError):
                pass

        self._task = None
        self._read_task = None
        self._save_settings()
        super().closeEvent(event)

    def _load_settings(self):
        s = QSettings("GeoPipe", "GeoPipePlugin")
        # Modo — deve vir primeiro pois dispara _on_mode_changed via sinal toggled
        mode = s.value("mode", "padrao")
        if mode == "declividade":
            self._rb_decl.setChecked(True)
        elif mode == "dinamico":
            self._rb_din.setChecked(True)
        else:
            self._rb_padrao.setChecked(True)
        # Paths
        raster = s.value("raster_path", "")
        if raster:
            self._raster_cmb.setCurrentIndex(-1)
            self._raster_cmb.lineEdit().setText(raster)
        grid = s.value("grid_path", "")
        if grid:
            self._grid_cmb.setCurrentIndex(-1)
            self._grid_cmb.lineEdit().setText(grid)
        self._tiles_edit.setText(s.value("tiles_dir", ""))
        self._output_edit.setText(s.value("output_dir", ""))
        self._upload_edit.setText(s.value("upload_dir", ""))
        # Parâmetros de vetorização
        self._tol_edit.setText(s.value("tolerance", "0.0001"))
        default_workers = str(max(1, (os.cpu_count() or 4) - 2))
        self._workers_edit.setText(s.value("workers", default_workers))
        # Parâmetros de merge
        self._mb_edit.setText(s.value("mb", "300"))
        self._uf_edit.setText(s.value("uf", "BR"))
        # Seleção DN (após modo já ter sido restaurado e combobox populado)
        saved_dn = s.value("dn")
        if saved_dn is not None:
            for i in range(self._cmb_dn.count()):
                if str(self._cmb_dn.itemData(i)) == str(saved_dn):
                    self._cmb_dn.setCurrentIndex(i)
                    break
        # Campos de declividade
        faixa = s.value("faixa", "")
        if faixa:
            self._faixa_edit.setText(faixa)
        nome = s.value("nome", "")
        if nome:
            self._nome_edit.setText(nome)
        # Colunas dinâmicas
        dynamic_json = s.value("dynamic_cols", "[]")
        try:
            cols = json.loads(dynamic_json)
            for col in cols:
                r = self._tbl_dynamic.rowCount()
                self._tbl_dynamic.insertRow(r)
                self._tbl_dynamic.setItem(r, 0, QTableWidgetItem(col.get("nome", "")))
                self._tbl_dynamic.setItem(r, 1, QTableWidgetItem(col.get("tipo", "str")))
                self._tbl_dynamic.setItem(r, 2, QTableWidgetItem(col.get("tamanho", "254")))
                self._tbl_dynamic.setItem(r, 3, QTableWidgetItem(col.get("valor", "")))
        except Exception:
            pass
        # Etapas selecionadas — restaurado após modo (que reconstrói os checkboxes)
        step_states_json = s.value("step_states", "[]")
        try:
            step_states = json.loads(step_states_json)
            for i, chk in enumerate(self._step_checks):
                if i < len(step_states):
                    chk.setChecked(bool(step_states[i]))
        except Exception:
            pass
        # Limpeza de tiles
        cleanup = s.value("cleanup", True)
        self._chk_cleanup.setChecked(cleanup if isinstance(cleanup, bool) else cleanup == "true")
        keep_shp = s.value("keep_unzipped", False)
        self._chk_keep_shp.setChecked(keep_shp if isinstance(keep_shp, bool) else keep_shp == "true")

    def _save_settings(self):
        s = QSettings("GeoPipe", "GeoPipePlugin")
        s.setValue("raster_path", self._get_raster_path())
        s.setValue("grid_path", self._get_grid_path())
        s.setValue("tiles_dir", self._tiles_edit.text())
        s.setValue("output_dir", self._output_edit.text())
        s.setValue("upload_dir", self._upload_edit.text())
        if self._rb_decl.isChecked():
            s.setValue("mode", "declividade")
        elif self._rb_din.isChecked():
            s.setValue("mode", "dinamico")
        else:
            s.setValue("mode", "padrao")
        s.setValue("tolerance", self._tol_edit.text())
        s.setValue("workers", self._workers_edit.text())
        s.setValue("mb", self._mb_edit.text())
        s.setValue("uf", self._uf_edit.text())
        s.setValue("dn", str(self._cmb_dn.currentData()))
        s.setValue("faixa", self._faixa_edit.text())
        s.setValue("nome", self._nome_edit.text())
        cols = []
        for row in range(self._tbl_dynamic.rowCount()):
            cols.append({
                "nome": self._tbl_dynamic.item(row, 0).text() if self._tbl_dynamic.item(row, 0) else "",
                "tipo": self._tbl_dynamic.item(row, 1).text() if self._tbl_dynamic.item(row, 1) else "str",
                "tamanho": self._tbl_dynamic.item(row, 2).text() if self._tbl_dynamic.item(row, 2) else "254",
                "valor": self._tbl_dynamic.item(row, 3).text() if self._tbl_dynamic.item(row, 3) else "",
            })
        s.setValue("dynamic_cols", json.dumps(cols))
        s.setValue("step_states", json.dumps([chk.isChecked() for chk in self._step_checks]))
        s.setValue("cleanup", self._chk_cleanup.isChecked())
        s.setValue("keep_unzipped", self._chk_keep_shp.isChecked())

    # ------------------------------------------------------------------
    # Verificação de dependências
    # ------------------------------------------------------------------

    def _check_dependencies(self):
        missing = []
        for lib in _REQUIRED_LIBS:
            try:
                __import__(lib)
            except ImportError:
                missing.append(lib)
        if missing:
            QMessageBox.warning(
                self,
                "Dependências não instaladas",
                "As seguintes bibliotecas Python não foram encontradas no ambiente do QGIS:\n\n"
                + "\n".join(f"  • {lib}" for lib in missing)
                + "\n\nInstale via OSGeo4W Shell:\n"
                f"  pip install {' '.join(missing)}\n\n"
                "O pipeline falhará sem estas dependências.",
            )

    # ------------------------------------------------------------------
    # Log — abrir arquivo externo
    # ------------------------------------------------------------------

    def _on_log_path_set(self, path: str):
        self._last_log_path = path
        self._btn_open_log.setEnabled(True)

    def _open_last_log(self):
        if self._last_log_path and Path(self._last_log_path).exists():
            if sys.platform == "win32":
                os.startfile(self._last_log_path)
            else:
                import subprocess as _sp
                _sp.Popen(["xdg-open", self._last_log_path])
        else:
            QMessageBox.information(self, "Log", "Nenhum log disponível ainda.")

    # ------------------------------------------------------------------
    # Progresso intra-etapa
    # ------------------------------------------------------------------

    def _on_tile_progress(self, current: int, total: int):
        if total > 0:
            step_base = self._current_step_idx * 100
            within = int((current / total) * 100)
            new_val = step_base + within
            if new_val > self._progress.value():
                self._progress.setValue(new_val)

    # ------------------------------------------------------------------
    # Ler classes do raster (modo dinâmico)
    # ------------------------------------------------------------------

    def _load_raster_classes_bg(self) -> None:
        """Lê as classes únicas do raster em uma QgsTask."""

        raster_path = self._get_raster_path()

        if not raster_path:
            QMessageBox.critical(
                self,
                "Erro",
                "Selecione o raster primeiro.",
            )
            return

        raster_file = Path(raster_path)

        if not raster_file.is_file():
            QMessageBox.critical(
                self,
                "Erro",
                f"Raster de entrada não encontrado:\n{raster_file}",
            )
            return

        if _is_task_running(self._read_task):
            QMessageBox.information(
                self,
                "Leitura em andamento",
                "Já existe uma leitura de raster em andamento.",
            )
            return

        self._btn_ler_raster.setEnabled(False)
        self._btn_ler_raster.setText("Lendo...")
        self._log(f"Iniciando leitura das classes: {raster_file}")

        dialog = self

        class RasterClassesTask(QgsTask):
            """Tarefa para leitura assíncrona das classes do raster."""

            def __init__(self, path: Path) -> None:
                super().__init__(
                    "GeoPipe - Ler classes do raster",
                    QgsTask.CanCancel,
                )
                self._path = path
                self.classes: list[int] = []
                self.error_message: str = ""

            def run(self) -> bool:
                """Executa a leitura fora da thread principal."""

                try:
                    import numpy as np
                    import rasterio

                    unique_values: set[int] = set()

                    with rasterio.open(self._path) as source:
                        block_height, block_width = source.block_shapes[0]

                        blocks_x = (
                            source.width + block_width - 1
                        ) // block_width

                        blocks_y = (
                            source.height + block_height - 1
                        ) // block_height

                        total_blocks = max(blocks_x * blocks_y, 1)
                        processed_blocks = 0

                        for _, window in source.block_windows(1):
                            if self.isCanceled():
                                self.error_message = (
                                    "Leitura cancelada pelo usuário."
                                )
                                return False

                            array = source.read(1, window=window)

                            # Filtrar NoData e NaNs com segurança
                            if source.nodata is not None:
                                if np.isnan(source.nodata):
                                    valid_pixels = array[~np.isnan(array)]
                                else:
                                    valid_pixels = array[(array != source.nodata) & ~np.isnan(array)]
                            else:
                                valid_pixels = array[~np.isnan(array)]

                            if valid_pixels.size > 0:
                                values = np.unique(valid_pixels)

                                for value in values:
                                    if np.isfinite(value):
                                        unique_values.add(int(value))

                                # Proteção contra rasters contínuos (ex: MDE ou declividade contínua)
                                if len(unique_values) > 2000:
                                    self.error_message = (
                                        "O raster parece conter dados contínuos "
                                        "(mais de 2.000 valores únicos detectados).\n\n"
                                        "A vetorização por classes requer um raster "
                                        "classificado/discreto com poucas classes."
                                    )
                                    return False

                            processed_blocks += 1
                            progress = int(
                                processed_blocks * 100 / total_blocks
                            )
                            self.setProgress(min(progress, 99))

                    self.classes = sorted(unique_values)
                    self.setProgress(100)

                    return True

                except ImportError as exc:
                    self.error_message = (
                        "Não foi possível importar uma dependência "
                        f"necessária: {exc}"
                    )
                    return False

                except Exception as exc:
                    self.error_message = (
                        f"{type(exc).__name__}: {exc}"
                    )
                    return False

            def finished(self, result: bool) -> None:
                """Atualiza a interface na thread principal."""

                dialog._btn_ler_raster.setEnabled(True)
                dialog._btn_ler_raster.setText("Ler Raster")
                dialog._read_task = None

                if not result:
                    message = self.error_message or (
                        "A tarefa de leitura foi encerrada sem concluir."
                    )

                    dialog._log(
                        f"Erro ao ler as classes do raster: {message}"
                    )

                    QMessageBox.critical(
                        dialog,
                        "Erro ao ler raster",
                        message,
                    )
                    return

                dialog._cmb_dn.blockSignals(True)

                try:
                    dialog._cmb_dn.clear()

                    for raster_class in self.classes:
                        dialog._cmb_dn.addItem(
                            str(raster_class),
                            userData=raster_class,
                        )
                finally:
                    dialog._cmb_dn.blockSignals(False)

                if self.classes:
                    dialog._cmb_dn.setCurrentIndex(0)

                    dialog._log(
                        "Classes encontradas no raster: "
                        + ", ".join(map(str, self.classes))
                    )

                    if dialog.iface:
                        dialog.iface.messageBar().pushMessage(
                            "GeoPipe",
                            (
                                f"{len(self.classes)} classe(s) "
                                "encontrada(s) no raster."
                            ),
                            level=Qgis.Success,
                            duration=5,
                        )
                else:
                    dialog._log(
                        "A leitura foi concluída, mas nenhuma classe "
                        "válida foi encontrada."
                    )

                    QMessageBox.warning(
                        dialog,
                        "Raster sem classes",
                        (
                            "Nenhuma classe válida foi encontrada.\n\n"
                            "Verifique o valor NoData e a banda utilizada."
                        ),
                    )

        self._read_task = RasterClassesTask(raster_file)

        if self.iface:
            self._read_task.progressChanged.connect(
                lambda progress: self.iface.messageBar().pushMessage(
                    "GeoPipe",
                    f"Lendo classes do raster: {int(progress)}%",
                    level=Qgis.Info,
                    duration=1,
                )
            )

        QgsApplication.taskManager().addTask(self._read_task)