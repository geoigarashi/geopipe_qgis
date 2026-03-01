# -*- coding: utf-8 -*-
"""GeoPipe Plugin - classe principal de integracao com o QGIS."""

from pathlib import Path
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction

_PLUGIN_DIR = Path(__file__).resolve().parent


class GeoPipePlugin:
    """Classe principal do plugin GeoPipe para QGIS."""

    def __init__(self, iface):
        self.iface = iface
        self._dialog = None
        self._action = None

    def initGui(self):
        icon_path = str(_PLUGIN_DIR / "icon.png")
        self._action = QAction(
            QIcon(icon_path),
            "GeoPipe - Pipeline Geoespacial",
            self.iface.mainWindow(),
        )
        self._action.setToolTip("Abrir o Pipeline Geoespacial GeoPipe")
        self._action.triggered.connect(self.run)
        self.iface.addPluginToMenu("&GeoPipe", self._action)
        self.iface.addToolBarIcon(self._action)

    def unload(self):
        if self._action:
            self.iface.removePluginMenu("&GeoPipe", self._action)
            self.iface.removeToolBarIcon(self._action)
            self._action = None

    def run(self):
        from .geopipe_dialog import GeoPipeDialog
        if self._dialog is None or not self._dialog.isVisible():
            self._dialog = GeoPipeDialog(parent=self.iface.mainWindow(), iface=self.iface)
        self._dialog.show()
        self._dialog.raise_()
        self._dialog.activateWindow()