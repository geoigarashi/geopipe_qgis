"""Compactação de shapefiles em arquivos ZIP individuais.

Para cada shapefile na pasta de saída, agrupa todos os arquivos
associados (.shp, .shx, .dbf, .prj, .cpg, .qpj, .qix) e compacta
em um único arquivo ZIP pronto para upload/entrega.
"""

import os
import time
import zipfile
from pathlib import Path

# ── Configuração ───────────────────────────────────────────────────────
# Lê de variáveis de ambiente (GUI/pipeline) ou solicita via input()
INPUT_DIR = Path(
    os.environ.get("PIPE_OUTPUT_DIR")
    or input("Pasta com os shapefiles para compactar: ").strip()
)
_dn_class = os.environ.get("PIPE_TARGET_CLASS", "8")
OUTPUT_DIR = INPUT_DIR / f"Zips_Prontos_DN_{_dn_class}"

# Extensões que compõem um shapefile completo
SHAPEFILE_EXTENSIONS = [".shp", ".shx", ".dbf", ".prj", ".cpg", ".qpj", ".qix"]

BYTES_PER_MB = 1024 * 1024


def zipar_entregaveis() -> None:
    """Compacta cada shapefile e seus componentes em um arquivo ZIP."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    shapefiles = sorted(INPUT_DIR.glob("*.shp"))

    if not shapefiles:
        print("Nenhum arquivo .shp encontrado para compactar!")
        return

    print(
        f"Encontrados {len(shapefiles)} arquivos principais. Iniciando compactação..."
    )

    start_total = time.time()

    for i, shp_path in enumerate(shapefiles, 1):
        t0 = time.time()
        base_name = shp_path.stem
        zip_name = f"{base_name}.zip"
        zip_path = OUTPUT_DIR / zip_name

        files_to_zip = []
        original_size = 0

        for ext in SHAPEFILE_EXTENSIONS:
            sibling = shp_path.with_suffix(ext)
            if sibling.exists():
                files_to_zip.append(sibling)
                original_size += os.path.getsize(sibling)

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for file in files_to_zip:
                zf.write(file, arcname=file.name)

        final_size = os.path.getsize(zip_path)
        compression_ratio = (1 - (final_size / original_size)) * 100
        elapsed = time.time() - t0

        print(f"[{i}/{len(shapefiles)}] Compactado: {zip_name}")
        print(
            f"   Original: {original_size / BYTES_PER_MB:.2f} MB -> "
            f"Zip: {final_size / BYTES_PER_MB:.2f} MB"
        )
        print(f"   Redução: {compression_ratio:.1f}% | Tempo: {elapsed:.2f}s")

        # Remover arquivos originais após compactação bem-sucedida
        for file in files_to_zip:
            file.unlink()
        print(f"   🗑️  {len(files_to_zip)} arquivo(s) original(is) removido(s)")

    total_time = (time.time() - start_total) / 60
    print(f"\n--- Processo Finalizado em {total_time:.2f} minutos ---")
    print(f"Arquivos prontos para upload em: {OUTPUT_DIR}")


if __name__ == "__main__":
    zipar_entregaveis()
