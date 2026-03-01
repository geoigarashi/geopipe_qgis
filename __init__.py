# -*- coding: utf-8 -*-
"""GeoPipe QGIS Plugin - entrada obrigatorio para o Plugin Manager."""


def classFactory(iface):
    """Instancia e retorna o plugin principal.

    Args:
        iface: Referencia ao objeto QgisInterface fornecido pelo QGIS.
    """
    from .geopipe_plugin import GeoPipePlugin
    return GeoPipePlugin(iface)
