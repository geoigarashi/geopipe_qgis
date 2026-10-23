# 🚀 GeoPipe QGIS — v1.2.1

**Data de Lançamento:** 23 de outubro de 2026  
**Repositório:** [geopipe_qgis](https://github.com/geoigarashi/geopipe_qgis)  
**Autor:** Clayton Igarashi (<geoigarashi@gmail.com>)  

---

## ✨ Destaques da Versão 1.2.1

A versão **1.2.1** consolida a conformidade estrita aos schemas corporativos do Oracle SIG para classes de declividade e uso do solo, garantindo integridade de acentuação em arquivos DBF e alinhamento visual inteligente da interface com os padrões de atributos.

### 🛡️ Integridade de Encoding & Sanitização
* **Preservação do Descritor de Code Page (`.cpg`):** O script `prepare_for_upload.py` não mais exclui o arquivo `.cpg` ao empacotar o `shape.zip`. A manutenção do arquivo impede que o QGIS ou GDAL assumam o fallback do sistema operacional (Windows-1252), mantendo a integridade de qualquer caractere.
* **Sanitização ASCII Estrita:** Remoção preventiva de acentos em textos descritivos padrão de declividade e atributos customizados via `unicodedata.normalize()`, garantindo 100% de compatibilidade com sistemas legados de banco de dados e drivers DBF.
* **Escrita Explícita com UTF-8:** Todas as chamadas de consolidação de atributos via Fiona declaram explicitamente `encoding="utf-8"`.

### 🎛️ Usabilidade & UX Dinâmica
* **Visibilidade Contextual de UF:** O campo `UF:` é dinamicamente ocultado quando a classe ativa ou modo não utiliza UF no schema corporativo:
  * **Oculto em:** Modo Declividade e Classes 1, 2, 3, 4, 6 e 10 de Uso do Solo (Benfeitorias e Agrícolas do padrão BB Valoração).
  * **Visível em:** Classes 8 (Matas Nativas/RL/APP), 9 (Corpos d'água) e 5 (Pastagem Degradada).
  * O rótulo da descrição se adapta automaticamente para `CLASSES_BB:` quando a classe selecionada pertencer a esse grupo.

---

## 📋 Changelog Detalhado

### Bug Fixes
* Corrigida a perda de acentos na coluna `FAIXA` decorrente da remoção inadvertida do arquivo `.cpg` na etapa de preparação para upload.
* Corrigida a dissonância na interface onde o campo `UF` continuava acessível em modos que não gravam a coluna no Shapefile.
* Adicionada exportação com `encoding="utf-8"` explícito em todos os módulos de merge.

### Refactoring & Code Quality
* Textos padrão em `DECLIV_DEFAULTS` convertidos para formato ASCII limpo (sem cedilhas ou acentos).
* Rotina `strip_accents()` adicionada em `merge_custom_attrs.py` para normalização em runtime.
* 100% de conformidade com o linter Ruff e boas práticas PyQGIS.

---

## 📦 Instalação e Atualização

1. No QGIS: **Complementos / Plugins** → **Gerenciar e Instalar Plugins...** → **Instalar a partir do ZIP**.
2. Selecione o arquivo compilado:
   `c:\Python\QGIS Plugins\plugins_zip\geopipe_qgis_v1.2.1.zip`
3. Clique em **Instalar plugin**.
