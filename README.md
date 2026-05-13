# Firecast BIB Converter

Conversor de arquivos `.bib` do **Firecast (RRPG)** para **HTML** ou **Markdown**, com três interfaces equivalentes: desktop (Tkinter), web (Flask) e executável Windows (`.exe`).

> Mantém a hierarquia de pastas do Firecast (ex: *Arco 4 → Sessões 1-9 → Sessão 1.1*) e ordena as sessões numericamente (1, 1.1, 2, 2.1, ..., 9, 9.1, 10, ...), independente da ordem em que aparecem internamente no arquivo binário.

---

## Sumário

- [Funcionalidades](#funcionalidades)
- [Como funciona](#como-funciona)
- [Requisitos](#requisitos)
- [Instalação](#instalação)
- [Uso](#uso)
  - [GUI desktop (Tkinter)](#1-gui-desktop-tkinter)
  - [Interface web (Flask)](#2-interface-web-flask)
  - [Executável Windows](#3-executável-windows)
- [Formatos de saída](#formatos-de-saída)
- [Gerar o `.exe`](#gerar-o-exe)
- [Estrutura do projeto](#estrutura-do-projeto)
- [Contribuindo](#contribuindo)
- [Licença](#licença)

---

## Funcionalidades

- **Conversão `.bib` → HTML** com estilo dark, cores originais e formatação preservadas
- **Conversão `.bib` → Markdown** (negrito, itálico, hierarquia de cabeçalhos)
- **Ordenação numérica automática** das sessões (corrige o problema de o Firecast armazenar sessões fora de ordem)
- **Agrupamento por pastas** (Arcos, "Sessões 1-9", etc.) detectados no arquivo
- **Lote**: converte vários `.bib` de uma vez (GUI)
- **Drag & drop** na GUI desktop
- **Três interfaces** para o mesmo motor de conversão

## Como funciona

O Firecast armazena cada arquivo `.bib` como:

1. Um cabeçalho de 21 bytes
2. Um bloco zlib que, ao ser descomprimido, contém uma sequência de itens
3. Cada item tem um marcador binário (`\xcd\xab\x85\x11`), um tipo (pasta ou sessão), um nome em UTF-8 e — no caso de sessões — um XML com o texto e metadados

O conversor:

1. Lê o arquivo binário e localiza o bloco zlib
2. Descomprime e percorre todos os itens (pastas e sessões)
3. Agrupa as sessões pela pasta imediatamente anterior na ordem de armazenamento
4. Ordena as sessões dentro de cada pasta pelo número extraído do nome (`Sessão 1`, `Sessão 1.1`, `Sessão 2`, ...)
5. Para cada sessão, faz parse do XML e converte os parágrafos para o formato de saída

## Requisitos

- **Python 3.10+**
- Dependências em `requirements.txt`:
  - `flask>=3.0`     — backend da interface web
  - `flaskwebgui>=1.0` — empacotamento do executável com janela nativa
  - `tkinterdnd2>=0.3` — drag & drop opcional na GUI desktop

## Instalação

```bash
git clone https://github.com/JonasDCampos/firecast-bib-converter.git
cd firecast-bib-converter
pip install -r requirements.txt
```

## Uso

### 1. GUI desktop (Tkinter)

```bash
python bib_converter.py
```

Selecione um ou mais arquivos `.bib` (ou arraste para a janela), escolha entre **HTML** ou **Markdown**, clique em **⚙ Converter**. Os arquivos resultantes são salvos no mesmo diretório do `.bib` de origem.

### 2. Interface web (Flask)

```bash
python server.py
```

Acesse `http://localhost:5000`, arraste o `.bib`, escolha o formato e clique em **Converter**. O download começa automaticamente.

### 3. Executável Windows

Se você baixou ou compilou o `BIB-Converter.exe`, basta dar dois cliques. Ele abre a interface web em uma janela nativa (sem barra de URL).

## Formatos de saída

| Característica | HTML | Markdown |
|---|---|---|
| Cores do texto original | preservadas | descartadas (Markdown puro) |
| Negrito (`b`) | `<span style="font-weight:bold">` | `**texto**` |
| Itálico (`i`) | `<span style="font-style:italic">` | `_texto_` |
| Pastas | `<h2 class="folder-header">` | `## Folder` |
| Sessões | `<div class="session-header">` | `### Sessão` |
| Tema | escuro (Firecast-like) | depende do viewer |

A escolha entre HTML e Markdown é feita na interface (radio button "Formato de saída").

## Gerar o `.exe`

A partir do código-fonte, com [PyInstaller](https://pyinstaller.org/) instalado:

```bash
pip install pyinstaller
build_exe.bat
```

O script usa `BIB-Converter.spec` e produz `dist/BIB-Converter.exe`.

## Estrutura do projeto

```
firecast-bib-converter/
├── bib_converter.py     # Núcleo de conversão + GUI Tkinter
├── server.py            # Interface web (Flask)
├── app_webview.py       # Entry point do .exe (Flask + flaskwebgui)
├── BIB-Converter.spec   # Receita do PyInstaller
├── build_exe.bat        # Atalho para gerar o .exe
├── requirements.txt
├── README.md
└── .gitignore
```

Funções principais em `bib_converter.py`:

- `find_items(data)` — identifica pastas e sessões no payload descomprimido
- `group_sessions(items)` — agrupa sessões por pasta e ordena numericamente
- `convert_bib_to_html(path)` — escreve `.html` ao lado do `.bib`
- `convert_bib_to_markdown(path)` — escreve `.md` ao lado do `.bib`
- `convert_bib(path, fmt)` — dispatch entre os dois formatos

## Contribuindo

Issues e PRs são bem-vindos. Casos especialmente úteis:

- Arquivos `.bib` que **não convertem corretamente** (anexe o arquivo se possível, ou um exemplo mínimo)
- Nomes de sessões que **não casam com o regex de ordenação** (atualmente: `Sess[ãa]o\s+(\d+)(?:\.(\d+))?`)
- Sugestões de formatos adicionais (TXT puro, PDF, etc.)

## Licença

Este projeto não possui licença explícita ainda. Consulte o autor antes de redistribuir.

---

*Não associado oficialmente ao Firecast / RRPG. Engenharia reversa do formato `.bib` feita por observação.*
