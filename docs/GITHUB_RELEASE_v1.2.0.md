# 🔄 GeoPipe QGIS — v1.2.0

**Data de Lançamento:** 01 de outubro de 2026  
**Repositório:** [geopipe_qgis](https://github.com/geoigarashi/geopipe_qgis)  
**Autor:** Clayton Igarashi (<geoigarashi@gmail.com>)  

---

## ✨ Destaques da Versão 1.2.0

A versão **1.2.0** traz um salto crítico em estabilidade, segurança de concorrência e velocidade de processamento geoespacial. Foram eliminados travamentos no carregamento de classes, processos zumbis de multiprocessamento no Windows, deadlocks silenciosos e gargalos severos de memória.

### ⚡ Performance & Otimização
* **Pré-Filtro Espacial da Grade (`vetorize_multicpu.py`):** A grade de articulação agora é filtrada espacialmente pela extensão geográfica (`bbox`) do raster antes do processamento paralelo. Tiles fora da mancha de dados não são mais despachados desnecessariamente para a fila de CPU.
* **Merge Vetorizado de Alta Velocidade (`merge_with_attributes.py` e `merge_dynamic_attrs.py`):** A verificação de geometrias gigantes (> 450k vértices) foi reescrita com contagem vetorizada em C via `shapely.get_num_coordinates`. O loop de `iterrows()` e cópias excessivas de memória sobre conjuntos normais foram eliminados.

### 🛡️ Concorrência e Robustez
* **Leitura Assíncrona Segura de Classes:** Reimplementação completa de `RasterClassesTask` com retenção segura no ciclo de vida do Qt (`QgsTask`), suporte adequado a `finished()`, filtragem de `NoData` com `np.isnan()` e proteção contra congelamento ao carregar rasters contínuos (MDE/declividade).
* **Finalização Limpa em Árvore de Processos:** Cancelar o pipeline no Windows agora executa `taskkill /F /T /PID`, encerrando de forma determinística todos os workers de CPU do `ProcessPoolExecutor`.
* **Proteção contra Deadlock de I/O em Scripts:** Substituição de bloqueios por `input()` pela rotina `_get_required_env()`, prevenindo travamentos sem log quando executados em segundo plano.
* **Interceptação de Fechamento (`closeEvent`):** Proteção no diálogo principal para alertar e cancelar tarefas em andamento antes do fechamento, prevenindo falhas de segmentação por referências C++ órfãs no QGIS.

### 🎛️ Usabilidade & Interface
* **Detecção Automática de Workers:** O campo `Max Workers` calcula dinamicamente a sugestão com base nos núcleos disponíveis no hardware da máquina: `max(1, (os.cpu_count() - 2))`.
* **Validação de Nomes de Campo DBF:** No modo Livre/Personalizado, nomes de coluna que excedam 10 caracteres ou contenham caracteres inválidos são alertados e prevenidos antes da gravação do Shapefile.
* **Preservação Configurável de Shapefiles:** Novo controle na interface para permitir a manutenção dos arquivos `.shp` descompactados após a geração do `.zip` (para quem prefere inspeção direta).
* **Padronização das Pastas de Upload:** Saída em pastas sequenciais formatadas com 3 dígitos (`001/shape.zip`, `002/shape.zip`, ...).

---

## 📋 Changelog Detalhado

### Features
* Adicionado checkbox *"Preservar shapefiles descompactados após compactação (.zip)"* com persistência em `QSettings`.
* Implementada validação de limites dBASE III (10 caracteres ASCII) na tabela dinâmica de atributos.
* Sugestão inteligente de número de workers baseada na CPU.

### Bug Fixes
* Corrigido congelamento permanente com texto "Lendo..." ao inspecionar classes do raster de entrada.
* Corrigido vazamento de processos zumbis consumindo CPU após cancelamento no Windows.
* Corrigido deadlock silencioso em scripts em segundo plano por tentativa de leitura de `stdin`.
* Corrigido conflito de DLLs e sequestro de `proj_9.dll` no Windows (`ImportError: DLL load failed while importing _context: The specified procedure could not be found`) ao disparar subprocessos sob presença de Miniconda/Anaconda no PATH.
* Adicionada rotina de bootstrap (`_env_bootstrap.py`) com pré-carregamento de `pyproj`, higienização de `PATH`, injeção explícita de `PYTHONHOME`, `PROJ_DATA` e `GDAL_DATA`.
* Corrigida seleção de camadas em GeoPackages (`.gpkg`), selecionando a camada vetorial ativa e silenciando avisos de `layer_styles`.
* Corrigida comparação de CRS em `vetorize_multicpu.py`, suportando grades sem CRS (`crs is None`) de forma defensiva antes da reprojeção.
* Corrigida numeração das pastas de upload em `prepare_for_upload.py` para padrão de 3 dígitos.

### Refactoring & Code Quality
* 100% de conformidade com o linter Ruff (PEP 8) e type hinting moderno Python 3.12+.
* Removidos imports não utilizados em todos os módulos.
* Sincronização do Grafo de Conhecimento (`graphify update`).

---

## 📦 Instalação e Atualização

1. No QGIS: **Complementos / Plugins** → **Gerenciar e Instalar Plugins...** → **Instalar a partir do ZIP**.
2. Selecione o arquivo compilado:
   `c:\Python\QGIS Plugins\plugins_zip\geopipe_qgis_v1.2.0.zip`
3. Clique em **Instalar plugin**.
