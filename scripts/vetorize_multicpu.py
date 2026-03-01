"""Vetorização multi-CPU de raster para shapefile por tile.

Lê um raster classificado e uma grade de articulação, extrai polígonos
da classe-alvo para cada tile em paralelo usando ProcessPoolExecutor,
aplica simplificação Douglas-Peucker e salva como shapefiles individuais.
"""

import concurrent.futures
import os
import sys
import time
from pathlib import Path

# Correção automática para GDAL_DATA (evita avisos no Windows/Conda)
if "GDAL_DATA" not in os.environ:
    gdal_data = Path(sys.exec_prefix) / "Library" / "share" / "gdal"
    if gdal_data.exists():
        os.environ["GDAL_DATA"] = str(gdal_data)

import fiona
import geopandas as gpd
import rasterio
import rasterio.mask
from rasterio.features import shapes
from shapely.geometry import shape
from shapely.geometry.polygon import orient

# ── Configuração ───────────────────────────────────────────────────────
# Lê de variáveis de ambiente (GUI/pipeline) ou solicita via input()
RASTER_PATH = (
    os.environ.get("PIPE_RASTER_PATH")
    or input("Caminho do raster de entrada (.tif): ").strip()
)
GRID_PATH = (
    os.environ.get("PIPE_GRID_PATH")
    or input("Caminho da grade de articulação (.shp): ").strip()
)
OUTPUT_DIR = (
    os.environ.get("PIPE_TILES_DIR") or input("Pasta de saída para os tiles: ").strip()
)
TARGET_CLASS = int(os.environ.get("PIPE_TARGET_CLASS", "8"))

# Simplificação Douglas-Peucker (tolerância em graus, ~11 m no equador)
SIMPLIFY_TOLERANCE = float(os.environ.get("PIPE_SIMPLIFY_TOL", "0.0001"))

# Ajuste conforme seu hardware.
# Ryzen 9 9950X3D: 16 cores / 32 threads. 30 é um bom valor.
MAX_WORKERS = int(os.environ.get("PIPE_MAX_WORKERS", "30"))


def processar_tile_worker(feature_geometry: dict, tile_id: int) -> str:
    """Processa um tile individual: recorta, vetoriza e salva.

    Cada worker abre sua própria leitura do raster para evitar
    race conditions no multiprocessamento.

    Args:
        feature_geometry: Geometria GeoJSON do tile (dicionário).
        tile_id: Identificador numérico do tile.

    Returns:
        Mensagem de status indicando sucesso, erro ou tile ignorado.
    """
    try:
        with rasterio.open(RASTER_PATH) as src:
            try:
                out_image, out_transform = rasterio.mask.mask(
                    src, [feature_geometry], crop=True
                )
            except ValueError:
                return f"Tile {tile_id}: Fora do Raster (Ignorado)"

            # Cria máscara binária para a classe alvo
            mask = out_image[0] == TARGET_CLASS

            results = (
                {"properties": {"raster_val": v}, "geometry": s}
                for _, (s, v) in enumerate(
                    shapes(out_image[0], mask=mask, transform=out_transform)
                )
            )

            geoms = list(results)
            if not geoms:
                return f"Tile {tile_id}: Sem geometrias válidas"

            polygons = [
                orient(
                    shape(g["geometry"]).simplify(
                        SIMPLIFY_TOLERANCE, preserve_topology=True
                    )
                )
                for g in geoms
            ]

            gdf = gpd.GeoDataFrame(
                {"cd_uso": [TARGET_CLASS] * len(polygons)},
                geometry=polygons,
                crs=src.crs,
            )

            out_name = f"Classe{TARGET_CLASS}_Tile_{tile_id}.shp"
            out_path = os.path.join(OUTPUT_DIR, out_name)
            gdf.to_file(out_path)

            size_mb = os.path.getsize(out_path) / (1024 * 1024)
            return f"SUCESSO: Tile {tile_id} | Polígonos: {len(gdf)} | {size_mb:.2f} MB"

    except Exception as exc:
        return f"ERRO no Tile {tile_id}: {exc}"


def main_paralelo() -> None:
    """Orquestra o processamento paralelo de todos os tiles."""
    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)

    print(f"Lendo grade de articulação: {GRID_PATH}")
    with fiona.open(GRID_PATH, "r") as grade:
        # Verificação básica de CRS
        with rasterio.open(RASTER_PATH) as src:
            # Normalizar strings WKT/Proj para comparação simples
            # (Ideal seria usar pyproj, mas vamos tentar string match ou aviso)
            print(f"Raster CRS: {src.crs}")
            print(f"Grid CRS: {grade.crs}")

            # Não abortamos para não parar tudo se for falso positivo, mas avisamos
            if str(src.crs) != str(grade.crs):
                print("⚠️  AVISO CRÍTICO: CRS do Raster e do Grid parecem diferentes!")
                print(
                    "   Isso pode causar desalinhamento, reamostragem ou perda de dados."
                )

        tasks = []
        for i, feature in enumerate(grade):
            t_id = feature["properties"].get("id", i)
            t_geom = feature["geometry"]
            tasks.append((t_geom, t_id))

    total_tasks = len(tasks)
    print(
        f"Iniciando processamento paralelo com {MAX_WORKERS} workers "
        f"para {total_tasks} tiles."
    )
    print("Isso vai acelerar o cooler da sua máquina. Monitore a temperatura.")

    start_time = time.time()

    with concurrent.futures.ProcessPoolExecutor(
        max_workers=MAX_WORKERS,
    ) as executor:
        futures = {
            executor.submit(processar_tile_worker, geom, tid): tid
            for geom, tid in tasks
        }

        completed_count = 0
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            completed_count += 1

            # Exibe apenas logs relevantes ou erros
            if "SUCESSO" in result or "ERRO" in result or "DEBUG" in result:
                print(f"[{completed_count}/{total_tasks}] {result}")

            if completed_count % 50 == 0:
                elapsed = time.time() - start_time
                rate = completed_count / elapsed
                print(
                    f"--- Progresso: {completed_count}/{total_tasks} "
                    f"({rate:.2f} tiles/seg) ---"
                )

    end_time = time.time()
    print(f"\nConcluído em {(end_time - start_time) / 60:.2f} minutos.")


if __name__ == "__main__":
    # Proteção obrigatória para multiprocessamento no Windows
    main_paralelo()
