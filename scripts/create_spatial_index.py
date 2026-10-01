"""Geração de índices espaciais (.qix) para shapefiles.

Percorre todos os shapefiles da pasta de saída e cria índices espaciais
quadtree (.qix) usando OGR/GDAL, melhorando a performance de consultas
espaciais em GIS e bancos de dados.
"""

import os
import sys
import time
from pathlib import Path

# Bootstrap e higienização de ambiente (PATH, PROJ_DATA, GDAL_DATA)
try:
    import _env_bootstrap  # noqa: F401
except ImportError:
    pass

from osgeo import ogr


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
INPUT_DIR = Path(_get_required_env("PIPE_OUTPUT_DIR", "Pasta com os shapefiles (.shp): "))



def gerar_indices_espaciais() -> None:
    """Cria índice espacial .qix para cada shapefile encontrado."""
    print("--- Iniciando Geração de Índices Espaciais (.qix) ---")

    shapefiles = sorted(INPUT_DIR.glob("*.shp"))

    if not shapefiles:
        print("Nenhum shapefile encontrado!")
        return

    ogr.UseExceptions()

    sucesso = 0
    erros = 0
    t0_total = time.time()

    for i, shp_path in enumerate(shapefiles, 1):
        filename = shp_path.name
        t0 = time.time()

        try:
            # Abre o Shapefile em modo de escrita (update=1)
            ds = ogr.Open(str(shp_path), 1)

            if ds is None:
                print(f"[{i}/{len(shapefiles)}] ❌ Falha ao abrir: {filename}")
                erros += 1
                continue

            layer = ds.GetLayer()
            layer_name = layer.GetName()

            # Cria o índice quadtree via SQL do OGR
            ds.ExecuteSQL(f'CREATE SPATIAL INDEX ON "{layer_name}"')

            # Fecha o dataset para salvar em disco
            ds = None

            elapsed = time.time() - t0
            print(
                f"[{i}/{len(shapefiles)}] ✅ Índice criado: {filename} ({elapsed:.2f}s)"
            )
            sucesso += 1

        except Exception as exc:
            print(f"[{i}/{len(shapefiles)}] ❌ Erro em {filename}: {exc}")
            erros += 1

    total_time = (time.time() - t0_total) / 60
    print(f"\n--- Processo Finalizado em {total_time:.2f} minutos ---")
    print(f"Sucessos: {sucesso} | Erros: {erros}")
    print("Nota: Verifique se os arquivos .qix apareceram na pasta.")


if __name__ == "__main__":
    gerar_indices_espaciais()
