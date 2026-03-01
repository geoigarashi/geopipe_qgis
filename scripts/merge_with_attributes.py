"""Merge de tiles vetorizados com padronização de tabela de atributos.

Consolida os shapefiles individuais (tiles) em partes de tamanho
controlado, padroniza a tabela de atributos com schema compatível
Oracle SIG e grava usando engine Fiona.
"""

import os
import sys
import time
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import MultiPolygon, Polygon, box

# Correção automática para GDAL_DATA (evita avisos no Windows/Conda)
if "GDAL_DATA" not in os.environ:
    gdal_data = Path(sys.exec_prefix) / "Library" / "share" / "gdal"
    if gdal_data.exists():
        os.environ["GDAL_DATA"] = str(gdal_data)

# ── Configuração ───────────────────────────────────────────────────────
# Lê de variáveis de ambiente (GUI/pipeline) ou solicita via input()
INPUT_DIR = Path(
    os.environ.get("PIPE_TILES_DIR")
    or input("Pasta com os tiles de entrada (.shp): ").strip()
)
OUTPUT_DIR = Path(
    os.environ.get("PIPE_OUTPUT_DIR")
    or input("Pasta de saída (entrega final): ").strip()
)
TARGET_SIZE_MB = int(os.environ.get("PIPE_TARGET_SIZE_MB", "300"))

BYTES_PER_MB = 1024 * 1024
MAX_VERTICES_PER_POLYGON = 450000


def split_polygon(poly: Polygon | MultiPolygon) -> list[Polygon]:
    """Recusively splits a polygon into smaller parts if it exceeds max vertices."""
    # Count total vertices across all parts of the geometry
    if isinstance(poly, Polygon):
        vertex_count = len(poly.exterior.coords) + sum(
            len(i.coords) for i in poly.interiors
        )
    elif isinstance(poly, MultiPolygon):
        vertex_count = sum(
            len(p.exterior.coords) + sum(len(i.coords) for i in p.interiors)
            for p in poly.geoms
        )
    else:
        return [poly]

    if vertex_count <= MAX_VERTICES_PER_POLYGON:
        return [poly]

    print(f"    ! Dividindo polígono gigante com {vertex_count:,} vértices...")

    parts = []
    minx, miny, maxx, maxy = poly.bounds
    cx, cy = (minx + maxx) / 2.0, (miny + maxy) / 2.0

    # Split across the longest axis
    if (maxx - minx) > (maxy - miny):
        box1 = box(minx, miny, cx, maxy)
        box2 = box(cx, miny, maxx, maxy)
    else:
        box1 = box(minx, miny, maxx, cy)
        box2 = box(minx, cy, maxx, maxy)

    for b in [box1, box2]:
        try:
            intersection = poly.intersection(b)
            if not intersection.is_empty:
                if isinstance(intersection, Polygon):
                    parts.extend(split_polygon(intersection))
                elif isinstance(intersection, MultiPolygon):
                    for geom in intersection.geoms:
                        parts.extend(split_polygon(geom))
                else:
                    # Drop lines/points if created by precision issues
                    pass
        except Exception:
            pass  # Ignore topology errors on weird geometries

    return parts


def merge_and_save(file_list: list[Path], part_number: int, fid_start: int) -> int:
    """Faz merge de um lote de tiles e salva com schema Oracle SIG.

    Args:
        file_list: Lista de caminhos dos shapefiles do lote.
        part_number: Número sequencial da parte (para nomear o arquivo).
        fid_start: Primeiro FID a ser atribuído neste lote.

    Returns:
        Próximo FID disponível para o lote seguinte.
    """
    print(f"\n--- Processando Parte {part_number:03d} ---")

    t0 = time.time()
    gdfs = []

    # 1. Leitura rápida com Pyogrio
    print(f"1. Carregando {len(file_list)} tiles...")
    for fp in file_list:
        gdf = gpd.read_file(fp, engine="pyogrio")
        gdfs.append(gdf)

    # 2. União em memória e quebra de excesso de vértices
    print("2. Unindo geometrias e analisando limite de vértices (max 450k)...")
    merged_gdf = gpd.GeoDataFrame(pd.concat(gdfs, ignore_index=True), crs=gdfs[0].crs)

    # 2.1 Verificar polígonos com mais de 450k vértices
    new_geometries = []
    has_split = False

    for _, row in merged_gdf.iterrows():
        geom = row["geometry"]
        if geom is None or geom.is_empty:
            continue

        parts = split_polygon(geom)
        if len(parts) > 1:
            has_split = True

        for p in parts:
            if not p.is_empty:
                new_row = row.copy()
                new_row["geometry"] = p
                new_geometries.append(new_row)

    if has_split:
        print("    -> Polígonos gigantes foram detectados e divididos com sucesso.")
        merged_gdf = gpd.GeoDataFrame(new_geometries, crs=gdfs[0].crs)

    qtd_poligonos = len(merged_gdf)
    del gdfs

    # 3. Padronização da tabela de atributos
    print("3. Padronizando tabela de atributos...")
    if "cd_uso" in merged_gdf.columns:
        merged_gdf.rename(columns={"cd_uso": "DN"}, inplace=True)

    dn_class_str = os.environ.get("PIPE_TARGET_CLASS", "8")
    merged_gdf["DN"] = int(dn_class_str)

    dn_class = int(dn_class_str)
    bb_classes = [1, 2, 3, 4, 6, 10]

    is_bb_class = dn_class in bb_classes

    if is_bb_class:
        merged_gdf["CLASSES_BB"] = os.environ.get("PIPE_DESCRICAO", "")
        merged_gdf["fid"] = list(range(fid_start, fid_start + qtd_poligonos))
        merged_gdf["fid"] = merged_gdf["fid"].astype(float)  # forçar float pro Fiona
        merged_gdf = merged_gdf[["fid", "DN", "CLASSES_BB", "geometry"]]
    else:
        merged_gdf["Descricao"] = os.environ.get(
            "PIPE_DESCRICAO", "Area de preservacao (RL,APP)"
        )
        merged_gdf["UF"] = os.environ.get("PIPE_UF", "BR")
        merged_gdf["fid"] = list(range(fid_start, fid_start + qtd_poligonos))
        merged_gdf = merged_gdf[["fid", "DN", "Descricao", "UF", "geometry"]]

    # 4. Escrita com schema Oracle SIG
    out_name = f"LULC_BR_Classe{dn_class_str}_Parte_{part_number:03d}.shp"
    out_path = OUTPUT_DIR / out_name

    if is_bb_class:
        oracle_schema = {
            "geometry": merged_gdf.geometry.geom_type.mode()[0],
            "properties": [
                ("fid", "float:20.0"),
                ("DN", "int:10"),
                ("CLASSES_BB", "str:254"),
            ],
        }
    else:
        oracle_schema = {
            "geometry": merged_gdf.geometry.geom_type.mode()[0],
            "properties": [
                ("fid", "int:15"),
                ("DN", "int:10"),
                ("Descricao", "str:254"),
                ("UF", "str:2"),
            ],
        }

    print(f"4. Gravando {qtd_poligonos:,} polígonos com schema Oracle SIG...")
    try:
        merged_gdf.to_file(out_path, engine="fiona", schema=oracle_schema)

        t1 = time.time()
        final_size = os.path.getsize(out_path) / BYTES_PER_MB
        print(f"✅ SUCESSO: {out_name}")
        print(
            f"   Tamanho: {final_size:.2f} MB | "
            f"Polígonos: {qtd_poligonos:,} | "
            f"Tempo: {t1 - t0:.2f} segundos"
        )
    except Exception as exc:
        print(f"❌ ERRO na Parte {part_number}: {exc}")

    return fid_start + qtd_poligonos


def consolidar_com_turbo() -> None:
    """Consolida todos os tiles em partes de tamanho controlado."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("Listando arquivos tiles...")
    shapefiles = sorted(INPUT_DIR.glob("*.shp"))

    if not shapefiles:
        print("ERRO: Nenhum shapefile encontrado!")
        return

    print(f"Total de tiles encontrados: {len(shapefiles)}")

    batch_files: list[Path] = []
    current_batch_size = 0
    part_counter = 1
    global_fid_counter = 1

    for shp_path in shapefiles:
        file_size = os.path.getsize(shp_path)

        if (current_batch_size + file_size) > (TARGET_SIZE_MB * BYTES_PER_MB):
            if batch_files:
                global_fid_counter = merge_and_save(
                    batch_files, part_counter, global_fid_counter
                )
                part_counter += 1
                batch_files = []
                current_batch_size = 0

        batch_files.append(shp_path)
        current_batch_size += file_size

    if batch_files:
        global_fid_counter = merge_and_save(
            batch_files, part_counter, global_fid_counter
        )

    print("\n--- Consolidação Finalizada com Sucesso ---")
    print(f"Total de Polígonos Processados: {global_fid_counter - 1:,}")


if __name__ == "__main__":
    consolidar_com_turbo()
