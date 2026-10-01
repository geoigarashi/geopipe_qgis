"""Padronização de shapefiles compactados para upload no banco de dados.

Para cada arquivo ZIP gerado na etapa de compactação, extrai os arquivos,
renomeia todos para 'shape.<ext>', compacta dentro de uma pasta 'shape/' e
salva como 'shape.zip' em pastas numeradas sequencialmente.

Uso: python prepare_for_upload.py
"""

import os
import shutil
import time
import zipfile
from pathlib import Path

import sys

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
_zips_env = os.environ.get("PIPE_ZIPS_DIR", "").strip()
if not _zips_env:
    _output_env = os.environ.get("PIPE_OUTPUT_DIR", "").strip()
    if _output_env:
        # Pipeline completo: tenta subfolder padrão criado pela etapa 4
        _dn = os.environ.get("PIPE_TARGET_CLASS", "8")
        _candidate = Path(_output_env) / f"Zips_Prontos_DN_{_dn}"
        if _candidate.is_dir() and any(_candidate.glob("*.zip")):
            _zips_env = str(_candidate)
        else:
            # Step 5 solo: usa PIPE_OUTPUT_DIR diretamente
            _zips_env = _output_env
    else:
        _zips_env = _get_required_env(
            "PIPE_ZIPS_DIR", "Pasta com os arquivos ZIP de entrada: "
        )

INPUT_DIR = Path(_zips_env)

_upload_env = os.environ.get("PIPE_UPLOAD_DIR", "").strip()
if not _upload_env and not os.environ.get("PIPE_OUTPUT_DIR"):
    if sys.stdin and sys.stdin.isatty():
        _upload_env = input(
            f"Pasta de saída para upload (Enter = {INPUT_DIR.parent / 'Upload_Prontos'}): "
        ).strip()
OUTPUT_DIR = Path(_upload_env) if _upload_env else INPUT_DIR.parent / "Upload_Prontos"

BYTES_PER_MB = 1024 * 1024
EXCLUDED_EXTENSIONS = {".cpg", ".qix"}


def preparar_para_upload() -> None:
    """Padroniza cada ZIP em pastas numeradas com shape.zip."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    zip_files = sorted(INPUT_DIR.glob("*.zip"))

    if not zip_files:
        print(f"Nenhum arquivo .zip encontrado em: {INPUT_DIR}")
        return

    total = len(zip_files)
    print(f"Encontrados {total} arquivos ZIP. Iniciando padronização...")
    print(f"Entrada: {INPUT_DIR}")
    print(f"Saída:   {OUTPUT_DIR}\n")

    start_total = time.time()

    for i, zip_path in enumerate(zip_files, 1):
        t0 = time.time()
        numbered_dir = OUTPUT_DIR / f"{i:03d}"
        shape_dir = numbered_dir / "shape"
        shape_zip_path = numbered_dir / "shape.zip"

        # Limpar pasta numerada se já existir
        if numbered_dir.exists():
            shutil.rmtree(numbered_dir)

        shape_dir.mkdir(parents=True, exist_ok=True)

        # Extrair ZIP original para pasta temporária
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(shape_dir)

        # Remover arquivos com extensões indesejadas (.cpg, .qix)
        for file in list(shape_dir.iterdir()):
            if file.is_file() and file.suffix.lower() in EXCLUDED_EXTENSIONS:
                file.unlink()

        # Renomear todos os arquivos para shape.<ext>
        files_renamed = 0
        for file in list(shape_dir.iterdir()):
            if file.is_file():
                new_name = f"shape{file.suffix.lower()}"
                new_path = shape_dir / new_name
                file.rename(new_path)
                files_renamed += 1

        # Compactar a pasta shape/ em shape.zip
        original_size = sum(
            f.stat().st_size for f in shape_dir.iterdir() if f.is_file()
        )

        with zipfile.ZipFile(shape_zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for file in sorted(shape_dir.iterdir()):
                if file.is_file():
                    zf.write(file, arcname=f"shape/{file.name}")

        final_size = shape_zip_path.stat().st_size

        # Remover a pasta shape/ intermediária, mantendo apenas shape.zip
        shutil.rmtree(shape_dir)

        compression_ratio = (
            (1 - (final_size / original_size)) * 100 if original_size else 0
        )
        elapsed = time.time() - t0

        print(
            f"[{i}/{total}] {zip_path.name} → pasta {i:03d}/shape.zip"
            f"  ({files_renamed} arquivos)"
        )
        print(
            f"   Original: {original_size / BYTES_PER_MB:.2f} MB → "
            f"Zip: {final_size / BYTES_PER_MB:.2f} MB "
            f"(redução: {compression_ratio:.1f}%) | {elapsed:.2f}s"
        )

    total_time = (time.time() - start_total) / 60
    print(f"\n--- Processo Finalizado em {total_time:.2f} minutos ---")
    print(f"Total: {total} pastas criadas em: {OUTPUT_DIR}")


if __name__ == "__main__":
    preparar_para_upload()
