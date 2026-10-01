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

# Bootstrap e higienização de ambiente (PATH, PROJ_DATA, GDAL_DATA)
try:
    import _env_bootstrap  # noqa: F401
except ImportError:
    pass

import geopandas as gpd
import pyogrio
import rasterio
import rasterio.mask
from rasterio.features import shapes
from shapely.geometry import box, shape
from shapely.geometry.polygon import orient


def _get_required_env(var_name: str, prompt: str) -> str:
    """Recupera variável de ambiente obrigatória ou solicita entrada interativa."""
    val = os.environ.get(var_name, "").strip()
    if val:
        return val
    if sys.stdin and sys.stdin.isatty():
        val = input(prompt).strip()
        if val:
            return val
    raise RuntimeError(
        f"Configuração obrigatória ausente: variável de ambiente '{var_name}' não definida."
    )


# ── Configuração ───────────────────────────────────────────────────────
# Lê de variáveis de ambiente (GUI/pipeline) ou solicita via input() em TTY
RASTER_PATH = _get_required_env("PIPE_RASTER_PATH", "Caminho do raster de entrada (.tif): ")
GRID_PATH = _get_required_env("PIPE_GRID_PATH", "Caminho da grade de articulação (.shp): ")
OUTPUT_DIR = _get_required_env("PIPE_TILES_DIR", "Pasta de saída para os tiles: ")
TARGET_CLASS = int(os.environ.get("PIPE_TARGET_CLASS", "8"))

# Simplificação Douglas-Peucker (tolerância em graus, ~11 m no equador)
SIMPLIFY_TOLERANCE = float(os.environ.get("PIPE_SIMPLIFY_TOL", "0.0001"))

# Ajuste conforme seu hardware.
# Padrão conservador: 14 workers. Aumente se tiver mais núcleos disponíveis.
MAX_WORKERS = int(os.environ.get("PIPE_MAX_WORKERS", "14"))


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
            out_path = Path(OUTPUT_DIR) / out_name
            gdf.to_file(out_path)

            size_mb = out_path.stat().st_size / (1024 * 1024)
            return f"SUCESSO: Tile {tile_id} | Polígonos: {len(gdf)} | {size_mb:.2f} MB"

    except Exception as exc:
        return f"ERRO no Tile {tile_id}: {exc}"


def main_paralelo() -> None:
    """Orquestra o processamento paralelo de todos os tiles."""
    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)

    print(f"Lendo grade de articulação: {GRID_PATH}")
    # Leitura inteligente de GPKG com múltiplas camadas (evita ler layer_styles)
    layer_to_read: str | None = None
    if GRID_PATH.lower().endswith(".gpkg"):
        try:
            layers = pyogrio.list_layers(GRID_PATH)
            for row in layers:
                name, geom_type = row[0], row[1]
                if name != "layer_styles" and geom_type is not None:
                    layer_to_read = str(name)
                    break
        except Exception:
            layer_to_read = None

    if layer_to_read:
        gdf_grade = gpd.read_file(GRID_PATH, layer=layer_to_read)
    else:
        gdf_grade = gpd.read_file(GRID_PATH)

    with rasterio.open(RASTER_PATH) as src:
        print(f"Raster CRS: {src.crs}")
        print(f"Grid CRS: {gdf_grade.crs}")

        if gdf_grade.crs is None:
            print(f"  ⚠️  Grade sem CRS definido. Assumindo o mesmo CRS do raster: {src.crs}")
            gdf_grade = gdf_grade.set_crs(src.crs)
        elif gdf_grade.crs != src.crs:
            print(
                f"⚠️  CRS diferentes detectados. Reprojetando grade "
                f"({gdf_grade.crs} → {src.crs})..."
            )
            gdf_grade = gdf_grade.to_crs(src.crs)
            print("   Grade reprojetada com sucesso.")

        # Filtro espacial prévio: filtra apenas tiles que intersectam o bounding box do raster
        raster_bbox = box(*src.bounds)
        total_grade_tiles = len(gdf_grade)
        gdf_grade = gdf_grade[gdf_grade.intersects(raster_bbox)].copy()
        print(
            f"Tiles dentro da área do raster: {len(gdf_grade)} "
            f"(de {total_grade_tiles} na grade total)"
        )

    if gdf_grade.empty:
        print("⚠️  Nenhum tile da grade intersecta a área do raster. Processamento encerrado.")
        return

    tasks = []
    for seq_i, row in enumerate(gdf_grade.itertuples(index=False)):
        t_id = getattr(row, "id", None)
        if t_id is None:
            t_id = getattr(row, "ID", seq_i)
        t_geom = row.geometry.__geo_interface__
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
