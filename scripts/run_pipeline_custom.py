"""Pipeline Customizada: Vetorização + Merge com Atributos Específicos (FAIXA/NOME).

Este script orquestra:
1. Vetorização paralela (usando vetorize_multicpu.py)
2. Consolidação e criação de campos customizados (usando merge_custom_attrs.py)
"""

import os
import subprocess
import sys
import time
from pathlib import Path

# Caminhos dos scripts auxiliares
SCRIPT_DIR = Path(__file__).parent
VETORIZE_SCRIPT = SCRIPT_DIR / "vetorize_multicpu.py"
MERGE_SCRIPT = SCRIPT_DIR / "merge_custom_attrs.py"


def run_pipeline_custom() -> None:
    """Executa o pipeline completo de vetorização customizada."""
    print("=== INICIANDO PIPELINE CUSTOMIZADO (DECLIVIDADE) ===")

    # ── 1. Validação de Ambientes ──────────────────────────────────────
    required_vars = [
        "PIPE_RASTER_PATH",
        "PIPE_GRID_PATH",
        "PIPE_TILES_DIR",
        "PIPE_OUTPUT_DIR",
        "PIPE_TARGET_CLASS",
        "PIPE_ATTR_FAIXA",
        "PIPE_ATTR_NOME",
    ]

    missing = [v for v in required_vars if not os.environ.get(v)]
    if missing:
        print(f"ERRO: Variáveis de ambiente faltando: {missing}")
        sys.exit(1)

    print(f"Classe Alvo: {os.environ['PIPE_TARGET_CLASS']}")
    print(f"Faixa: {os.environ['PIPE_ATTR_FAIXA']}")
    print(f"Nome: {os.environ['PIPE_ATTR_NOME']}")
    print("-" * 50)

    # Correção automática para GDAL_DATA (evita avisos no Windows/Conda)
    if "GDAL_DATA" not in os.environ:
        gdal_data = Path(sys.exec_prefix) / "Library" / "share" / "gdal"
        if gdal_data.exists():
            os.environ["GDAL_DATA"] = str(gdal_data)

    t_start = time.time()

    # ── 2. Vetorização (Reusa script existente) ────────────────────────
    print("\n>>> ETAPA 1/2: Vetorização Paralela")
    try:
        # Chamamos o mesmo interpretador Python atual
        subprocess.run(
            [sys.executable, str(VETORIZE_SCRIPT)],
            check=True,
            env=os.environ.copy(),  # Passa as variáveis para o subprocesso
        )
    except subprocess.CalledProcessError as e:
        print(f"\n❌ ERRO na vetorização: {e}")
        sys.exit(1)

    # ── 3. Merge Customizado (Novo script) ─────────────────────────────
    print("\n>>> ETAPA 2/2: Consolidação e Atributos")
    try:
        subprocess.run(
            [sys.executable, str(MERGE_SCRIPT)], check=True, env=os.environ.copy()
        )
    except subprocess.CalledProcessError as e:
        print(f"\n❌ ERRO na consolidação customizada: {e}")
        sys.exit(1)

    # ── 4. Finalização ─────────────────────────────────────────────────
    elapsed = (time.time() - t_start) / 60
    print(f"\n✅ PIPELINE CUSTOMIZADO CONCLUÍDO em {elapsed:.2f} minutos.")


if __name__ == "__main__":
    run_pipeline_custom()
