"""Merge de tiles vetorizados com suporte a schema 100% customizável.

Lê os tiles, junta em partes e constrói a tabela de atributos
exatamente como especificado pelo usuário na interface gráfica via
variável de ambiente PIPE_CUSTOM_SCHEMA.
"""

import json
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
INPUT_DIR = Path(
    os.environ.get("PIPE_TILES_DIR")
    or input("Pasta com os tiles de entrada (.shp): ").strip()
)
OUTPUT_DIR = Path(
    os.environ.get("PIPE_OUTPUT_DIR")
    or input("Pasta de saída (entrega final): ").strip()
)
TARGET_SIZE_MB = int(os.environ.get("PIPE_TARGET_SIZE_MB", "300"))
CUSTOM_SCHEMA_JSON = os.environ.get("PIPE_CUSTOM_SCHEMA", "[]")

try:
    CUSTOM_SCHEMA = json.loads(CUSTOM_SCHEMA_JSON)
except json.JSONDecodeError:
    print("ERRO: PIPE_CUSTOM_SCHEMA não contém um JSON válido.")
    CUSTOM_SCHEMA = []

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
    """Faz merge de um lote de tiles e aplica schema customizável."""
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

    # 3. Construindo tabela de atributos customizável
    print("3. Construindo tabela de atributos customizada...")

    # Criar DataFrame vazio apenas com fids para receber as colunas novas
    fids = list(range(fid_start, fid_start + qtd_poligonos))
    out_df = pd.DataFrame({"fid": fids})

    # Preencher colunas baseadas no schema fornecido
    oracle_properties = []

    has_fid = False
    has_dn = False

    for col in CUSTOM_SCHEMA:
        nome = str(col["nome"]).strip()
        tipo_base = col["tipo"]
        tamanho = col["tamanho"]
        valor = col["valor"]

        if not nome:
            continue
        if nome.lower() == "fid":
            has_fid = True
            # Se o usuário informou o nome FID (maiúsculo) ou fId, precisamos
            # mapear o array gerado internamente como "fid" para esse nome exato.
            # O dicionário fids inicia como {"fid": [...]}, então se o nome
            # bater mas tiver case diferente, renomeamos a coluna interna.
            if nome != "fid" and "fid" in out_df.columns:
                out_df.rename(columns={"fid": nome}, inplace=True)

            # Se o usuário não preencheu valor constante, mantemos a numeração incremental já gerada
            if valor != "":
                if tipo_base == "int":
                    out_df[nome] = int(valor)
                elif tipo_base == "float":
                    out_df[nome] = float(valor)
                else:
                    out_df[nome] = str(valor)
            # Garantimos que fid venha no início do schema
            oracle_properties.insert(0, (nome, f"{tipo_base}:{tamanho}"))
            continue

        if nome.upper() == "DN":
            has_dn = True
            if valor != "":
                if tipo_base == "int":
                    try:
                        out_df[nome] = int(valor)
                    except ValueError:
                        out_df[nome] = 0
                elif tipo_base == "float":
                    try:
                        out_df[nome] = float(valor)
                    except ValueError:
                        out_df[nome] = 0.0
                else:
                    out_df[nome] = str(valor)
            else:
                try:
                    out_df[nome] = int(os.environ.get("PIPE_TARGET_CLASS", "0"))
                except ValueError:
                    out_df[nome] = 0
            oracle_properties.append((nome, f"{tipo_base}:{tamanho}"))
            continue

        # Converter o tipo corretamente no DataFrame
        if tipo_base == "int":
            try:
                out_df[nome] = int(valor)
            except ValueError:
                out_df[nome] = 0
            oracle_properties.append((nome, f"int:{tamanho}"))
        elif tipo_base == "float":
            try:
                out_df[nome] = float(valor)
            except ValueError:
                out_df[nome] = 0.0
            oracle_properties.append((nome, f"float:{tamanho}"))
        else:
            out_df[nome] = str(valor)
            oracle_properties.append((nome, f"str:{tamanho}"))

    # Injetar DN se não foi incluído explicitamente
    if not has_dn:
        try:
            dn_val = int(os.environ.get("PIPE_TARGET_CLASS", "0"))
        except ValueError:
            dn_val = 0
        out_df["DN"] = dn_val
        oracle_properties.append(("DN", "int:10"))

    # Recuperar o fid padrão do oracle_properties se não preenchido
    if not has_fid:
        oracle_properties.insert(0, ("fid", "float:20.0"))

    # Monta o GeoDataFrame Final (Tirando tudo o que Pyogrio/Rasterio gerou e deixando apenas os fids e geometry do merge, mais as novas colunas)
    out_gdf = gpd.GeoDataFrame(
        out_df, geometry=merged_gdf.geometry.values, crs=merged_gdf.crs
    )

    # 4. Escrita com schema Oracle SIG
    out_name = f"Custom_Output_Parte_{part_number:03d}.shp"
    out_path = OUTPUT_DIR / out_name

    oracle_schema = {
        "geometry": out_gdf.geometry.geom_type.mode()[0],
        "properties": oracle_properties,
    }

    print(f"4. Gravando {qtd_poligonos:,} polígonos com schema customizado...")
    try:
        out_gdf.to_file(out_path, engine="fiona", schema=oracle_schema)

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


def consolidar() -> None:
    """Consolida todos os tiles em partes de tamanho controlado."""
    if not CUSTOM_SCHEMA:
        print("ERRO: Nenhum campo customizado foi definido!")
        return

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
    consolidar()
