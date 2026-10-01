"""Bootstrap de ambiente e resolução de DLLs Geoespaciais (Windows/OSGeo4W/Conda).

Garante que DLLs nativas (PROJ, GDAL, GEOS) e bases de dados cartográficos
(proj.db, gcs.csv) sejam carregadas dos caminhos corretos sem conflito de
versões com outros ambientes Python presentes no PATH do sistema.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def bootstrap_environment() -> None:
    """Configura PATH e caminhos de dados de GDAL e PROJ no runtime."""
    prefix = Path(sys.prefix)
    exec_prefix = Path(sys.exec_prefix)
    osgeo4w_root = prefix.parent.parent

    # ── 1. Higienização e Priorização de PATH no Windows ─────────────────────
    if sys.platform == "win32":
        raw_path = os.environ.get("PATH", "").split(os.pathsep)
        clean_path: list[str] = []

        is_osgeo4w = (osgeo4w_root / "bin" / "proj_9.dll").exists() or (
            osgeo4w_root / "bin" / "o4w_env.bat"
        ).exists()

        for item in raw_path:
            stripped = item.strip()
            if not stripped:
                continue
            item_lower = stripped.lower()
            # Impede o carregamento de DLLs com ABI conflitante (ex: Miniconda no PATH do Windows)
            if is_osgeo4w and ("miniconda" in item_lower or "anaconda" in item_lower):
                continue
            clean_path.append(stripped)

        priority_dirs = [
            str(osgeo4w_root / "bin"),
            str(prefix / "Scripts"),
            str(prefix),
            str(osgeo4w_root / "apps" / "qgis-ltr" / "bin"),
        ]
        final_priority = [d for d in priority_dirs if Path(d).is_dir()]
        for d in reversed(final_priority):
            if d in clean_path:
                clean_path.remove(d)
            clean_path.insert(0, d)

        os.environ["PATH"] = os.pathsep.join(clean_path)

    # ── 2. PROJ_DATA e PROJ_LIB ──────────────────────────────────────────────
    current_proj = os.environ.get("PROJ_DATA") or os.environ.get("PROJ_LIB")
    if not current_proj or not (Path(current_proj) / "proj.db").exists():
        proj_candidates = [
            osgeo4w_root / "share" / "proj",
            exec_prefix.parent.parent / "share" / "proj",
            prefix / "Library" / "share" / "proj",
            prefix / "share" / "proj",
        ]
        for p_cand in proj_candidates:
            if (p_cand / "proj.db").exists():
                os.environ["PROJ_DATA"] = str(p_cand)
                os.environ["PROJ_LIB"] = str(p_cand)
                break

    # ── 3. GDAL_DATA e Plugins ───────────────────────────────────────────────
    current_gdal = os.environ.get("GDAL_DATA")
    if not current_gdal or not Path(current_gdal).is_dir():
        gdal_candidates = [
            osgeo4w_root / "apps" / "gdal" / "share" / "gdal",
            exec_prefix.parent.parent / "apps" / "gdal" / "share" / "gdal",
            prefix / "Library" / "share" / "gdal",
            prefix / "share" / "gdal",
        ]
        for g_cand in gdal_candidates:
            if g_cand.is_dir():
                os.environ["GDAL_DATA"] = str(g_cand)
                break

    # ── 4. Flags de Encoding ─────────────────────────────────────────────────
    os.environ["PYTHONUTF8"] = "1"
    os.environ["PYTHONIOENCODING"] = "utf-8"

    # ── 5. Pré-inicialização segura de pyproj ────────────────────────────────
    # Garante que _context.pyd carregue as DLLs corretas antes de pyogrio/fiona
    try:
        import pyproj  # noqa: F401
    except Exception:
        pass


bootstrap_environment()
