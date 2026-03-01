# GeoPipe – Pipeline Geoespacial para QGIS

Plugin QGIS para execução automatizada do pipeline completo de vetorização multi-CPU, merge com tabela de atributos, criação de índice espacial, compactação e preparação de shapefiles para upload.

---

## Screenshots

| Modo Uso do Solo | Modo Declividade | Modo Livre/Personalizado |
|---|---|---|
| ![Uso do Solo](docs/screenshot_padrao.png) | ![Declividade](docs/screenshot_declividade.png) | ![Livre](docs/screenshot_livre.png) |

---

## Funcionalidades

- **3 modos de pipeline** configuráveis na interface:
  - **Uso do Solo** — classes DN com adaptação BB Valoração (agricultura, pastagem, silvicultura, etc.)
  - **Declividade** — campos FAIXA e NOME customizados por faixa de declividade
  - **Livre/Personalizado** — definição dinâmica de colunas com nome, tipo, tamanho e valor constante

- **Seleção de camadas abertas** — raster de entrada e grade de articulação podem ser escolhidos diretamente das camadas carregadas no projeto QGIS, ou buscados pelo sistema de arquivos

- **Execução em background** — pipeline roda em `QgsTask` (thread segura), mantendo o QGIS responsivo durante o processamento

- **Log em arquivo** — gerado automaticamente na pasta de upload (ou pasta de saída) a cada execução: `pipeline_YYYYMMDD_HHMMSS.log`

- **Cancelamento** — interrompe o processo em andamento a qualquer momento

- **Validação de caminhos** — verifica existência de arquivos e pastas antes de iniciar; oferece criação automática de pastas faltantes

- **Carga automática de resultado** — ao concluir com sucesso, carrega o shapefile resultante diretamente no mapa do QGIS (suporta leitura de `shape.zip` via `/vsizip/` sem extração)

- **Limpeza de tiles** — opção para remover tiles intermediários ao final do pipeline

---

## Etapas do Pipeline

| # | Script | Descrição |
|---|---|---|
| 1 | `vetorize_multicpu.py` | Vetorização multi-CPU com Douglas-Peucker |
| 2 | `merge_with_attributes.py` / `merge_custom_attrs.py` / `merge_dynamic_attrs.py` | Merge dos tiles + tabela de atributos |
| 3 | `create_spatial_index.py` | Criação de índice espacial `.qix` |
| 4 | `compress_shapefiles.py` | Compactação em ZIP |
| 5 | `prepare_for_upload.py` | Padronização em pastas numeradas com `shape.zip` |

Cada etapa pode ser ativada/desativada individualmente antes da execução.

---

## Requisitos

### QGIS
- QGIS ≥ 3.16 (LTR recomendado)
- Instalação via OSGeo4W

### Python (ambiente OSGeo4W)
As dependências abaixo devem estar instaladas no **Python do QGIS** (`OSGeo4W\bin\python.exe`), não no Python do sistema:

```
fiona
rasterio
geopandas
shapely
pandas
numpy
pyogrio
```

Para instalar via OSGeo4W Shell:
```bash
pip install fiona rasterio geopandas shapely pandas numpy pyogrio
```

---

## Instalação

### Opção 1 — Instalar pelo ZIP (recomendado)

1. Faça o download do repositório como `.zip` (`Code → Download ZIP`)
2. No QGIS: **Plugins → Gerenciar e Instalar Plugins → Instalar a partir de um ZIP**
3. Selecione o arquivo baixado e clique em **Instalar Plugin**

### Opção 2 — Instalação manual

1. Clone ou baixe este repositório
2. Copie a pasta `geopipe_qgis/` para o diretório de plugins do QGIS:
   ```
   %APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\
   ```
3. Reinicie o QGIS
4. Ative o plugin em **Plugins → Gerenciar e Instalar Plugins → Instalados**

---

## Como Usar

1. Abra o plugin pelo menu **GeoPipe** ou pelo ícone na barra de ferramentas
2. **Arquivos e Pastas** — selecione o raster de entrada e a grade de articulação (camadas abertas no QGIS ou arquivos no disco) e defina as pastas de saída
3. **Tipo de Pipeline** — escolha o modo adequado ao seu dado
4. **Vetorização** — selecione a classe DN e ajuste os parâmetros (tolerância Douglas-Peucker, número de workers)
5. **Merge / Tabela de Atributos** — preencha os atributos conforme o modo selecionado
6. **Etapas a Executar** — marque as etapas desejadas
7. Clique em **▶ Executar**

O log de execução é exibido em tempo real na seção **Log** e salvo em arquivo na pasta de upload.

---

## Estrutura do Projeto

```
geopipe_qgis/
├── __init__.py              # classFactory → instancia GeoPipePlugin
├── metadata.txt             # metadados do Plugin Manager do QGIS
├── geopipe_plugin.py        # registra botão/ação no menu e toolbar
├── geopipe_dialog.py        # diálogo principal (PyQt5) + PipelineTask (QgsTask)
├── icon.png                 # ícone do plugin
├── docs/                    # screenshots e documentação
└── scripts/                 # scripts do pipeline (chamados via subprocess)
    ├── vetorize_multicpu.py
    ├── merge_with_attributes.py
    ├── merge_custom_attrs.py
    ├── merge_dynamic_attrs.py
    ├── create_spatial_index.py
    ├── compress_shapefiles.py
    └── prepare_for_upload.py
```

---

## Notas Técnicas

- Os scripts são chamados via `subprocess.Popen` com variáveis de ambiente `PIPE_*`, sem qualquer alteração nos scripts originais
- O executável `python.exe` é localizado automaticamente no ambiente OSGeo4W (não usa `sys.executable`, que no QGIS aponta para `qgis-ltr.exe`)
- A leitura de shapefiles dentro de `.zip` após o upload é feita via `/vsizip/` (GDAL nativo), sem extração de arquivos

---

## Autor

**Clayton Igarashi** — [geoigarashi@gmail.com](mailto:geoigarashi@gmail.com)

Repositório: [https://github.com/geoigarashi/geopipe_qgis](https://github.com/geoigarashi/geopipe_qgis)

---

## Licença

Este projeto é distribuído sob a licença [GNU GPL v2](https://www.gnu.org/licenses/old-licenses/gpl-2.0.html), em conformidade com os requisitos de plugins QGIS.
