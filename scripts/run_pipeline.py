"""Pipeline completo: executa todos os scripts na ordem correta.

Uso: python run_pipeline.py

- Mostra a saída de cada script em tempo real no terminal
- Gera log em: <projeto>/logs/pipeline_YYYYMMDD_HHMMSS.log
- Gera relatório CSV em: <projeto>/logs/pipeline_YYYYMMDD_HHMMSS.csv
"""

import csv
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# ── Configuração ───────────────────────────────────────────────────────
_PROJECT_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = _PROJECT_DIR / "logs"
SCRIPT_DIR = _PROJECT_DIR / "scripts"

# Scripts na ordem de execução
PIPELINE = [
    ("vetorize_multicpu.py", "Vetorização Multi-CPU"),
    ("merge_with_attributes.py", "Merge + Tabela de Atributos"),
    ("create_spatial_index.py", "Índice Espacial (.qix)"),
    ("compress_shapefiles.py", "Compactação em Zip"),
]


def run_pipeline() -> None:
    """Executa o pipeline sequencialmente com log e relatório CSV."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = LOG_DIR / f"pipeline_{timestamp}.log"
    csv_file = LOG_DIR / f"pipeline_{timestamp}.csv"

    log_fh = open(log_file, "w", encoding="utf-8")

    def log(msg: str) -> None:
        """Imprime no terminal E grava no arquivo de log."""
        print(msg, flush=True)
        log_fh.write(msg + "\n")
        log_fh.flush()

    log("=" * 60)
    log("  PIPELINE DE PROCESSAMENTO GEOESPACIAL")
    log(f"  Início: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log(f"  Log:    {log_file}")
    log(f"  CSV:    {csv_file}")
    log("=" * 60)

    total_start = time.time()
    resultados = []

    for i, (script, descricao) in enumerate(PIPELINE, 1):
        log(f"\n{'=' * 60}")
        log(f"  ETAPA {i}/{len(PIPELINE)}: {descricao}")
        log(f"  Script: {script}")
        log(f"{'=' * 60}\n")

        step_start = time.time()
        inicio_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"

        process = subprocess.Popen(
            [sys.executable, "-u", script],
            cwd=str(SCRIPT_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )

        for line in process.stdout:
            line = line.rstrip("\n\r")
            log(f"  {line}")

        process.wait()

        step_elapsed = time.time() - step_start
        step_min = step_elapsed / 60
        fim_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if process.returncode != 0:
            status = "FALHA"
            log(f"\n❌ FALHA na etapa {i}: {descricao}")
            log(f"   Código de saída: {process.returncode}")
            log(f"   Pipeline interrompido após {step_min:.2f} minutos nesta etapa.")
        else:
            status = "OK"
            log(
                f"\n✅ Etapa {i} concluída em "
                f"{step_min:.2f} minutos ({step_elapsed:.0f}s)"
            )

        resultados.append(
            {
                "etapa": i,
                "descricao": descricao,
                "script": script,
                "status": status,
                "inicio": inicio_str,
                "fim": fim_str,
                "tempo_segundos": round(step_elapsed, 2),
                "tempo_minutos": round(step_min, 2),
            }
        )

        if status == "FALHA":
            break

    # ── Resumo ─────────────────────────────────────────────────────────
    total_elapsed = time.time() - total_start
    total_min = total_elapsed / 60

    log(f"\n{'=' * 60}")
    log("  RESUMO DO PIPELINE")
    log(f"{'=' * 60}")
    log(f"  {'Etapa':<35} {'Status':<8} {'Tempo':<12}")
    log(f"  {'-' * 35} {'-' * 8} {'-' * 12}")

    for r in resultados:
        log(f"  {r['descricao']:<35} {r['status']:<8} {r['tempo_minutos']:.2f} min")

    log(f"  {'-' * 57}")
    log(f"  TEMPO TOTAL: {total_min:.2f} minutos ({total_elapsed:.0f}s)")

    falhas = sum(1 for r in resultados if r["status"] == "FALHA")
    if falhas:
        log(f"  ⚠️  Pipeline encerrado com {falhas} falha(s).")
    else:
        log("  🎉 Pipeline completo sem erros!")

    log(f"\n  Log salvo em: {log_file}")
    log(f"  CSV salvo em: {csv_file}")

    log_fh.close()

    # ── Relatório CSV ──────────────────────────────────────────────────
    fieldnames = [
        "etapa",
        "descricao",
        "script",
        "status",
        "inicio",
        "fim",
        "tempo_segundos",
        "tempo_minutos",
    ]
    with open(csv_file, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=";")
        writer.writeheader()
        writer.writerows(resultados)
        writer.writerow(
            {
                "etapa": "",
                "descricao": "TOTAL",
                "script": "",
                "status": "OK" if not falhas else "FALHA",
                "inicio": resultados[0]["inicio"] if resultados else "",
                "fim": resultados[-1]["fim"] if resultados else "",
                "tempo_segundos": round(total_elapsed, 2),
                "tempo_minutos": round(total_min, 2),
            }
        )

    if falhas:
        sys.exit(1)


if __name__ == "__main__":
    run_pipeline()
