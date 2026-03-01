"""Merge de tiles vetorizados com tabela de atributos CUSTOMIZADA.

Consolida os shapefiles individuais (tiles) em partes de tamanho
controlado e padroniza a tabela de atributos com schema específico:
- fid (Real 20,0)
- DN (Real 23,15)
- FAIXA (String 254)
- NOME (String 254)
"""

import os
import sys
import time
from pathlib import Path

import geopandas as gpd
import pandas as pd

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

# Novos campos customizados
ATTR_FAIXA = os.environ.get("PIPE_ATTR_FAIXA", "")
ATTR_NOME = os.environ.get("PIPE_ATTR_NOME", "")
TARGET_CLASS = int(os.environ.get("PIPE_TARGET_CLASS", "8"))

BYTES_PER_MB = 1024 * 1024


def merge_and_save(file_list: list[Path], part_number: int, fid_start: int) -> int:
    """Faz merge de um lote de tiles e salva com schema customizado.

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
        try:
            gdf = gpd.read_file(fp, engine="pyogrio")
            if not gdf.empty:
                gdfs.append(gdf)
        except Exception as e:
            print(f"AVISO: Erro ao ler {fp.name}: {e}")

    if not gdfs:
        print("Nenhum dado válido neste lote.")
        return fid_start

    # 2. União em memória
    print("2. Unindo geometrias...")
    merged_gdf = gpd.GeoDataFrame(pd.concat(gdfs, ignore_index=True), crs=gdfs[0].crs)
    qtd_poligonos = len(merged_gdf)
    del gdfs

    # 3. Padronização da tabela de atributos
    print("3. Padronizando tabela de atributos (Schema Customizado)...")

    # Garante que temos as colunas certas e preenche valores
    # Schema solicitado: fid (Real), DN (Real), FAIXA (Str), NOME (Str)

    merged_gdf["fid"] = range(fid_start, fid_start + qtd_poligonos)
    merged_gdf["DN"] = float(TARGET_CLASS)  # Assegura float para 23.15
    merged_gdf["FAIXA"] = str(ATTR_FAIXA)
    merged_gdf["NOME"] = str(ATTR_NOME)

    # Seleciona e ordena colunas
    merged_gdf = merged_gdf[["fid", "DN", "FAIXA", "NOME", "geometry"]]

    # 4. Escrita com schema específico
    out_name = f"DECLIV_Classe{TARGET_CLASS}_Parte_{part_number:03d}.shp"
    out_path = OUTPUT_DIR / out_name

    # Definição estrita do schema para o driver shapefile/Fiona
    # Ajuste: fid como int:18 (mais compatível que float:20.0 para IDs) ou float:20.0 se estrito.
    # O usuário pediu: fid (Real:20.0), DN (Real:23.15).

    # OBS: Shapefile tem limites. Float 23.15 pode ser problemático se o driver não suportar.
    # Vamos forçar o tipo nos dados antes de salvar.

    merged_gdf["fid"] = merged_gdf["fid"].astype(float)
    merged_gdf["DN"] = merged_gdf["DN"].astype(float)

    custom_schema = {
        "geometry": "Polygon",
        "properties": [
            ("fid", "float:20.0"),  # Real, tamanho 20, precisão 0
            (
                "DN",
                "float:24.15",
            ),  # Aumentado para 24.15 para contornar truncamento em 22
            ("FAIXA", "str:254"),  # String, 254
            ("NOME", "str:254"),  # String, 254
        ],
    }

    print(f"4. Gravando {qtd_poligonos:,} polígonos com schema customizado...")
    try:
        # engine="fiona" é necessário para respeitar o schema detalhado
        merged_gdf.to_file(out_path, engine="fiona", schema=custom_schema)

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

    print("Configuração Customizada:")
    print(f"  - Output Dir: {OUTPUT_DIR}")
    print(f"  - Classe Alvo: {TARGET_CLASS}")
    print(f"  - FAIXA: {ATTR_FAIXA}")
    print(f"  - NOME: {ATTR_NOME}")

    print("\nListando arquivos tiles...")
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
        try:
            file_size = os.path.getsize(shp_path)
        except OSError:
            continue

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

    print("\n--- Consolidação Customizada Finalizada ---")
    print(f"Total de Polígonos Processados: {global_fid_counter - 1:,}")


if __name__ == "__main__":
    consolidar_com_turbo()
