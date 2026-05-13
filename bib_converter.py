try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    HAS_DND = True
except ImportError:
    HAS_DND = False

import tkinter as tk
from tkinter import filedialog, ttk, scrolledtext
import zlib
import xml.etree.ElementTree as ET
import os
import re
import threading
from html import escape

# Binary markers inside the decompressed RRPG data
ITEM_MARKER  = b'\xcd\xab\x85\x11'                                  # precedes every folder/session entry
TYPE_FOLDER  = b'\x02\x00\x00\x00'
TYPE_SESSION = b'\x06\x03\x00\x00'
SEPARATOR    = b'\x96\xea\x02\x00\x00\x00\x00\x00\x96\xea\x95\x00'  # separates session header from XML
HEADER_SIZE  = 21


# ──────────────────────────────────────────────
# Parsing logic
# ──────────────────────────────────────────────

def color_to_css(color_str):
    """Convert $FFRRGGBB → #RRGGBB. Returns light grey on failure."""
    if color_str and color_str.startswith('$') and len(color_str) == 9:
        return '#' + color_str[3:]   # skip $FF (alpha byte)
    return '#e0e0e0'


def parse_xml_session(xml_bytes):
    """Parse one session's XML.

    Returns list of paragraphs; each paragraph is a list of dicts
    {'text': str, 'color': str, 'style': str}.
    """
    try:
        xml_str = xml_bytes.decode('utf-8', errors='replace').rstrip('\x00')
        xml_marker = xml_str.find('<?xml')
        if xml_marker > 0:
            xml_str = xml_str[xml_marker:]
        root = ET.fromstring(xml_str)
    except ET.ParseError:
        return []

    paragraphs = []
    txt_elem = root.find('txt')
    if txt_elem is None:
        return paragraphs

    for p in txt_elem.findall('p'):
        elements = []
        for e in p.findall('e'):
            elements.append({
                'text':  e.get('text',  ''),
                'color': e.get('color', '$FFE0E0E0'),
                'style': e.get('style', ''),
            })
        paragraphs.append(elements)

    return paragraphs


def find_items(data):
    """Walk the decompressed data and extract every folder / session entry.

    Format of each entry (observed by reverse-engineering):
        marker (4)         = \xcd\xab\x85\x11
        name_len (4, LE)
        type (4)           = \x02\x00\x00\x00 (folder) or \x06\x03\x00\x00 (session)
        padding (5)
        name (name_len bytes, UTF-8)
        — sessions are followed by SEPARATOR + 4-byte XML size + XML bytes

    Returns list of dicts:
        {'kind': 'folder'|'session', 'name': str, 'pos': int, 'xml': bytes (sessions only)}
    in storage order.
    """
    items = []

    positions = []
    pos = 0
    while True:
        p = data.find(ITEM_MARKER, pos)
        if p == -1:
            break
        positions.append(p)
        pos = p + 1

    for idx, mark_pos in enumerate(positions):
        after_marker = mark_pos + 4
        if after_marker + 8 > len(data):
            continue

        name_len   = int.from_bytes(data[after_marker:after_marker + 4], 'little')
        type_field = data[after_marker + 4:after_marker + 8]

        # 5-byte gap between type field and name
        name_start = after_marker + 8 + 5
        name_end   = name_start + name_len
        if name_end > len(data) or name_len <= 0 or name_len > 512:
            continue

        try:
            name = data[name_start:name_end].decode('utf-8', errors='replace').strip()
        except Exception:
            name = f'Item {idx + 1}'
        if not name:
            name = f'Item {idx + 1}'

        next_item_pos = positions[idx + 1] if idx + 1 < len(positions) else len(data)

        if type_field == TYPE_FOLDER:
            items.append({'kind': 'folder', 'name': name, 'pos': mark_pos})

        elif type_field == TYPE_SESSION:
            # SEPARATOR should sit right after the name
            sep_pos = name_end
            if data[sep_pos:sep_pos + len(SEPARATOR)] != SEPARATOR:
                fallback = data.find(SEPARATOR, name_end, name_end + 64)
                if fallback == -1:
                    continue
                sep_pos = fallback

            after_sep = sep_pos + len(SEPARATOR)
            if after_sep + 4 > len(data):
                continue
            size = int.from_bytes(data[after_sep:after_sep + 4], 'little')
            xml_start = after_sep + 4

            if size > 0 and xml_start + size <= next_item_pos:
                xml_data = data[xml_start:xml_start + size]
            else:
                xml_data = data[xml_start:next_item_pos]

            xml_data = xml_data.rstrip(b'\x00')
            if xml_data:
                items.append({'kind': 'session', 'name': name,
                              'pos': mark_pos, 'xml': xml_data})

    return items


_SESSION_NUM_RE = re.compile(r'Sess[ãa]o\s+(\d+)(?:\.(\d+))?', re.IGNORECASE)


def session_sort_key(name):
    """Build a sort key from the session name.

    'Sessão 1'        → (0, 1, 0, ...)
    'Sessão 1.1 - X'  → (0, 1, 1, ...)
    'Sessão 9.2'      → (0, 9, 2, ...)
    Names without a recognizable number sort to the end.
    """
    m = _SESSION_NUM_RE.search(name)
    if m:
        major = int(m.group(1))
        minor = int(m.group(2)) if m.group(2) else 0
        return (0, major, minor, name.lower())
    return (1, 0, 0, name.lower())


def group_sessions(items):
    """Group sessions under their preceding folder; sort sessions within each group.

    Returns list of tuples: [(folder_dict_or_None, [session_dict, ...]), ...]
    Folders are kept in storage order; sessions inside are numerically sorted.
    """
    groups = []
    current_folder   = None
    current_sessions = []
    folder_started   = False

    for it in items:
        if it['kind'] == 'folder':
            if current_sessions or folder_started:
                groups.append((current_folder, current_sessions))
            current_folder   = it
            current_sessions = []
            folder_started   = True
        else:
            current_sessions.append(it)

    groups.append((current_folder, current_sessions))
    # Drop groups that are empty AND have no folder header
    groups = [(f, s) for f, s in groups if s or f is not None]

    for _, sessions in groups:
        sessions.sort(key=lambda s: session_sort_key(s['name']))

    return groups


def find_sessions(data):
    """Backwards-compatible helper: returns [(name, xml_bytes), ...] correctly ordered."""
    items  = find_items(data)
    groups = group_sessions(items)
    out = []
    for _, sessions in groups:
        for s in sessions:
            out.append((s['name'], s['xml']))
    return out


def elements_to_html(elements):
    parts = []
    for elem in elements:
        text  = escape(elem['text'])
        color = color_to_css(elem['color'])
        style = elem['style']

        css = f'color:{color};'
        if 'b' in style:
            css += 'font-weight:bold;'
        if 'i' in style:
            css += 'font-style:italic;'

        parts.append(f'<span style="{css}">{text}</span>')
    return ''.join(parts)


HTML_HEAD = """\
<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; }}
    body {{
      background: #1a1a2e;
      color: #e0e0e0;
      font-family: Georgia, 'Times New Roman', serif;
      max-width: 920px;
      margin: 0 auto;
      padding: 30px 20px 60px;
      line-height: 1.7;
      font-size: 1rem;
    }}
    h1.doc-title {{
      color: #c9a84c;
      text-align: center;
      font-size: 1.6rem;
      letter-spacing: 3px;
      margin-bottom: 40px;
      border-bottom: 2px solid #c9a84c66;
      padding-bottom: 12px;
    }}
    h2.folder-header {{
      color: #c9a84c;
      font-size: 1.35rem;
      text-align: center;
      margin: 48px 0 24px;
      letter-spacing: 2px;
      text-transform: uppercase;
      border-top: 2px solid #c9a84c;
      border-bottom: 2px solid #c9a84c;
      padding: 12px 0;
      background: #12122044;
    }}
    .session-header {{
      color: #c9a84c;
      font-size: 1.15rem;
      font-weight: bold;
      text-align: center;
      padding: 12px 0 8px;
      margin: 36px 0 14px;
      border-top: 2px solid #c9a84c;
      border-bottom: 1px solid #c9a84c44;
      letter-spacing: 2px;
      text-transform: uppercase;
    }}
    .session-body {{
      background: #12122066;
      border-radius: 6px;
      padding: 14px 18px;
    }}
    .p {{
      margin: 3px 0;
      min-height: 1.1em;
      word-wrap: break-word;
    }}
    hr.divider {{
      border: none;
      border-top: 1px solid #2a2a4a;
      margin: 40px 0 0;
    }}
  </style>
</head>
<body>
<h1 class="doc-title">{title}</h1>
"""

HTML_FOOT = "</body>\n</html>\n"


def _decompress_bib(bib_path):
    """Read .bib, locate the zlib block, return the decompressed payload."""
    with open(bib_path, 'rb') as f:
        raw = f.read()

    search_area = raw[HEADER_SIZE:]
    zlib_pos = -1
    for magic in (b'\x78\xda', b'\x78\x9c', b'\x78\x01'):
        zlib_pos = search_area.find(magic)
        if zlib_pos != -1:
            break
    if zlib_pos == -1:
        raise ValueError("Bloco zlib não encontrado no arquivo.")

    return zlib.decompress(search_area[zlib_pos:])


def _load_grouped_sessions(bib_path):
    """Return (title, groups, total_count) ready for rendering."""
    decompressed = _decompress_bib(bib_path)
    items  = find_items(decompressed)
    groups = group_sessions(items)
    total  = sum(len(s) for _, s in groups)
    if total == 0:
        raise ValueError("Nenhuma sessão encontrada após a descompressão.")
    title = os.path.splitext(os.path.basename(bib_path))[0]
    return title, groups, total


def convert_bib_to_html(bib_path):
    """Read → decompress → parse → write HTML. Returns (html_path, session_count)."""
    title, groups, total = _load_grouped_sessions(bib_path)

    parts = [HTML_HEAD.format(title=escape(title))]
    session_idx = 0

    for folder, sessions in groups:
        if folder is not None:
            parts.append(f'<h2 class="folder-header">{escape(folder["name"])}</h2>')

        for s in sessions:
            if session_idx > 0:
                parts.append('<hr class="divider">')
            session_idx += 1
            parts.append(f'<div class="session-header">{escape(s["name"])}</div>')
            parts.append('<div class="session-body">')

            paragraphs = parse_xml_session(s['xml'])
            if paragraphs:
                for elems in paragraphs:
                    if not elems:
                        parts.append('<div class="p">&nbsp;</div>')
                    else:
                        parts.append('<div class="p">' + elements_to_html(elems) + '</div>')
            else:
                parts.append('<div class="p" style="color:#666;font-style:italic;">'
                             '(sem conteúdo legível)</div>')

            parts.append('</div>')

    parts.append(HTML_FOOT)

    html_path = os.path.splitext(bib_path)[0] + '.html'
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(parts))

    return html_path, total


# ──────────────────────────────────────────────
# Markdown converter
# ──────────────────────────────────────────────

_MD_ESCAPE_RE = re.compile(r'([\\`*_{}\[\]()#+\-.!>|])')


def _md_escape(text):
    """Escape Markdown-significant characters inside inline runs."""
    return _MD_ESCAPE_RE.sub(r'\\\1', text)


def elements_to_markdown(elements):
    """Convert a list of element dicts to a Markdown inline string.

    Bold → **text**, italic → _text_, colors are dropped.
    """
    parts = []
    for elem in elements:
        text  = elem['text']
        style = elem['style']
        if not text:
            continue

        out = _md_escape(text)
        # Keep wrapping whitespace outside emphasis markers so they hug visible text
        leading  = len(out) - len(out.lstrip())
        trailing = len(out) - len(out.rstrip())
        core = out[leading:len(out) - trailing] if trailing else out[leading:]
        prefix = out[:leading]
        suffix = out[len(out) - trailing:] if trailing else ''

        if core:
            if 'b' in style:
                core = f'**{core}**'
            if 'i' in style:
                core = f'_{core}_'

        parts.append(prefix + core + suffix)
    return ''.join(parts)


def convert_bib_to_markdown(bib_path):
    """Read → decompress → parse → write Markdown. Returns (md_path, session_count)."""
    title, groups, total = _load_grouped_sessions(bib_path)

    lines = [f'# {title}', '']

    for folder, sessions in groups:
        if folder is not None:
            lines.append(f'## {folder["name"]}')
            lines.append('')

        for s in sessions:
            lines.append(f'### {s["name"]}')
            lines.append('')

            paragraphs = parse_xml_session(s['xml'])
            if paragraphs:
                for elems in paragraphs:
                    if not elems:
                        lines.append('')
                    else:
                        lines.append(elements_to_markdown(elems))
            else:
                lines.append('_(sem conteúdo legível)_')
            lines.append('')
            lines.append('---')
            lines.append('')

    md_path = os.path.splitext(bib_path)[0] + '.md'
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

    return md_path, total


def convert_bib(bib_path, fmt='html'):
    """Dispatch to the chosen format. fmt ∈ {'html', 'markdown', 'md'}."""
    fmt = (fmt or 'html').lower()
    if fmt in ('md', 'markdown'):
        return convert_bib_to_markdown(bib_path)
    return convert_bib_to_html(bib_path)


# ──────────────────────────────────────────────
# GUI
# ──────────────────────────────────────────────

DARK_BG    = '#1a1a2e'
PANEL_BG   = '#16213e'
ACCENT     = '#c9a84c'
BTN_BG     = '#0f3460'
BTN_FG     = '#e0e0e0'
BTN_ACTIVE = '#1a5276'
TEXT_FG    = '#e0e0e0'
SUCCESS    = '#4caf7d'
ERROR      = '#e05c5c'
INFO       = '#7ec8e3'
DROP_HOVER = '#1e2a4a'


class BibConverterApp:
    def __init__(self, root):
        self.root = root
        self.root.title('Firecast BIB → HTML / Markdown Converter')
        self.root.configure(bg=DARK_BG)
        self.root.minsize(620, 560)
        self.root.resizable(True, True)

        self.selected_files = []
        self.output_format  = tk.StringVar(value='html')   # 'html' | 'markdown'
        self._build_ui()

        if HAS_DND:
            self._setup_dnd()

    # ── UI ───────────────────────────────────────────────────────────────

    def _build_ui(self):
        # Top bar
        top = tk.Frame(self.root, bg=PANEL_BG, pady=14)
        top.pack(fill='x')

        tk.Label(top, text='Firecast  BIB → HTML / Markdown',
                 bg=PANEL_BG, fg=ACCENT,
                 font=('Georgia', 16, 'bold')).pack()
        tk.Label(top, text='Converte arquivos de sessão do RRPG em HTML ou Markdown',
                 bg=PANEL_BG, fg='#888',
                 font=('Georgia', 9)).pack(pady=(2, 0))

        # Format selector
        fmt_frame = tk.Frame(self.root, bg=DARK_BG, pady=10, padx=16)
        fmt_frame.pack(fill='x')

        tk.Label(fmt_frame, text='Formato de saída:',
                 bg=DARK_BG, fg='#888',
                 font=('Segoe UI', 9)).pack(side='left', padx=(0, 12))

        for label, value in (('HTML', 'html'), ('Markdown', 'markdown')):
            rb = tk.Radiobutton(
                fmt_frame, text=label, variable=self.output_format, value=value,
                bg=DARK_BG, fg=TEXT_FG,
                activebackground=DARK_BG, activeforeground=ACCENT,
                selectcolor=BTN_BG, font=('Segoe UI', 9, 'bold'),
                cursor='hand2', highlightthickness=0, bd=0,
            )
            rb.pack(side='left', padx=(0, 12))

        # Buttons
        btn_frame = tk.Frame(self.root, bg=DARK_BG, pady=12, padx=16)
        btn_frame.pack(fill='x')

        self._make_btn(btn_frame, '📂  Selecionar arquivos',
                       self._select_files).pack(side='left', padx=(0, 8))

        self.convert_btn = self._make_btn(
            btn_frame, '⚙  Converter',
            self._start_conversion, state='disabled')
        self.convert_btn.pack(side='left', padx=(0, 8))

        self._make_btn(btn_frame, '🗑  Limpar',
                       self._clear, bg='#2a1a1a').pack(side='left')

        # Drop zone
        drop_label_text = (
            'Arraste arquivos .bib aqui  —  ou use o botão acima'
            if HAS_DND else
            'Use o botão acima para selecionar arquivos .bib'
        )
        drop_frame = tk.Frame(self.root, bg=DARK_BG, padx=16)
        drop_frame.pack(fill='x')

        self.drop_zone = tk.Label(
            drop_frame,
            text=drop_label_text,
            bg='#0d1117', fg='#444',
            font=('Segoe UI', 9),
            relief='flat',
            bd=0,
            pady=10,
            cursor='hand2' if HAS_DND else 'arrow',
        )
        self.drop_zone.pack(fill='x', pady=(0, 4))
        # dashed border effect via highlight
        self.drop_zone.config(highlightthickness=1,
                              highlightbackground='#2a2a4a',
                              highlightcolor=ACCENT)

        # File list
        list_frame = tk.Frame(self.root, bg=DARK_BG, padx=16)
        list_frame.pack(fill='x')

        tk.Label(list_frame, text='Arquivos na fila:',
                 bg=DARK_BG, fg='#888', font=('Consolas', 8)).pack(anchor='w')

        self.file_listbox = tk.Listbox(
            list_frame,
            height=4, selectmode='extended',
            bg='#0d1117', fg=TEXT_FG,
            selectbackground=BTN_BG, selectforeground=ACCENT,
            font=('Consolas', 9),
            bd=0, highlightthickness=1,
            highlightcolor=ACCENT, highlightbackground='#333',
            activestyle='none',
        )
        self.file_listbox.pack(fill='x', pady=(2, 0))

        sb = tk.Scrollbar(list_frame, orient='horizontal',
                          command=self.file_listbox.xview,
                          bg=PANEL_BG, troughcolor=DARK_BG,
                          highlightthickness=0)
        sb.pack(fill='x')
        self.file_listbox.configure(xscrollcommand=sb.set)

        # Progress bar
        prog_frame = tk.Frame(self.root, bg=DARK_BG, padx=16, pady=4)
        prog_frame.pack(fill='x')

        style = ttk.Style()
        style.theme_use('default')
        style.configure('gold.Horizontal.TProgressbar',
                        troughcolor='#0d1117', background=ACCENT,
                        bordercolor=DARK_BG, lightcolor=ACCENT, darkcolor=ACCENT)
        self.progress = ttk.Progressbar(
            prog_frame, style='gold.Horizontal.TProgressbar',
            mode='determinate')
        self.progress.pack(fill='x')

        # Log
        log_frame = tk.Frame(self.root, bg=DARK_BG, padx=16, pady=(4, 16))
        log_frame.pack(fill='both', expand=True)

        tk.Label(log_frame, text='Log:', bg=DARK_BG, fg='#888',
                 font=('Consolas', 8)).pack(anchor='w')

        self.log = scrolledtext.ScrolledText(
            log_frame,
            bg='#0d1117', fg=TEXT_FG,
            font=('Consolas', 9),
            bd=0, highlightthickness=1,
            highlightcolor='#333', highlightbackground='#333',
            state='disabled', wrap='word',
            insertbackground=ACCENT,
        )
        self.log.pack(fill='both', expand=True, pady=(2, 0))

        self.log.tag_config('info',    foreground=INFO)
        self.log.tag_config('success', foreground=SUCCESS)
        self.log.tag_config('error',   foreground=ERROR)
        self.log.tag_config('warn',    foreground=ACCENT)
        self.log.tag_config('dim',     foreground='#555')

        if HAS_DND:
            self._log('Drag & drop ativo — arraste arquivos .bib para a janela.', 'dim')
        else:
            self._log('Dica: instale tkinterdnd2 para habilitar drag & drop.', 'dim')

    def _make_btn(self, parent, text, cmd, state='normal', bg=BTN_BG):
        return tk.Button(
            parent, text=text, command=cmd,
            bg=bg, fg=BTN_FG,
            activebackground=BTN_ACTIVE, activeforeground=ACCENT,
            font=('Segoe UI', 9, 'bold'),
            relief='flat', bd=0,
            padx=14, pady=6,
            cursor='hand2',
            state=state,
            disabledforeground='#555',
        )

    # ── Drag-and-drop ─────────────────────────────────────────────────────

    def _setup_dnd(self):
        """Register drop target on the whole window and the drop zone label."""
        for widget in (self.root, self.drop_zone, self.file_listbox):
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind('<<Drop>>', self._on_drop)
            widget.dnd_bind('<<DragEnter>>', self._on_drag_enter)
            widget.dnd_bind('<<DragLeave>>', self._on_drag_leave)

    def _parse_drop_data(self, data):
        """Parse tkinterdnd2 drop data into a list of file paths.

        Handles: space-separated paths, paths wrapped in {braces}, quoted paths.
        """
        paths = []
        data = data.strip()

        # tkinterdnd2 wraps paths with spaces in { }
        import re
        tokens = re.findall(r'\{([^}]+)\}|(\S+)', data)
        for braced, plain in tokens:
            p = braced if braced else plain
            if p:
                paths.append(p)
        return paths

    def _on_drop(self, event):
        self.drop_zone.config(bg='#0d1117', fg='#444',
                              highlightbackground='#2a2a4a')
        raw_paths = self._parse_drop_data(event.data)
        bib_paths = [p for p in raw_paths
                     if os.path.isfile(p) and p.lower().endswith('.bib')]
        ignored   = len(raw_paths) - len(bib_paths)

        if not bib_paths:
            self._log('Nenhum arquivo .bib reconhecido no drop.', 'error')
            return

        self._add_files(bib_paths)
        if ignored:
            self._log(f'  ({ignored} arquivo(s) ignorado(s) — não são .bib)', 'dim')

    def _on_drag_enter(self, event):
        self.drop_zone.config(bg=DROP_HOVER, fg=ACCENT,
                              highlightbackground=ACCENT)

    def _on_drag_leave(self, event):
        self.drop_zone.config(bg='#0d1117', fg='#444',
                              highlightbackground='#2a2a4a')

    # ── File management ───────────────────────────────────────────────────

    def _add_files(self, paths):
        existing = set(self.selected_files)
        added = 0
        for p in paths:
            if p not in existing:
                self.selected_files.append(p)
                self.file_listbox.insert('end', p)
                existing.add(p)
                added += 1
        if added:
            self.convert_btn.config(state='normal')
            self._log(f'{added} arquivo(s) adicionado(s).', 'info')
        else:
            self._log('Arquivos já estão na fila.', 'dim')

    def _select_files(self):
        files = filedialog.askopenfilenames(
            title='Selecionar arquivos .bib do Firecast',
            filetypes=[('Arquivos BIB', '*.bib'), ('Todos os arquivos', '*.*')],
        )
        if files:
            self._add_files(list(files))

    def _clear(self):
        self.selected_files = []
        self.file_listbox.delete(0, 'end')
        self.convert_btn.config(state='disabled')
        self.progress['value'] = 0
        self._log('Lista limpa.', 'dim')

    # ── Conversion ────────────────────────────────────────────────────────

    def _start_conversion(self):
        if not self.selected_files:
            return
        self.convert_btn.config(state='disabled')
        self.progress['value'] = 0
        self.progress['maximum'] = len(self.selected_files)
        threading.Thread(target=self._convert_all, daemon=True).start()

    def _convert_all(self):
        total  = len(self.selected_files)
        ok     = 0
        failed = 0
        fmt    = self.output_format.get()
        fmt_label = 'Markdown' if fmt == 'markdown' else 'HTML'

        self._log(f'Iniciando conversão de {total} arquivo(s) para {fmt_label}…', 'info')

        for i, path in enumerate(self.selected_files, 1):
            fname = os.path.basename(path)
            self._log(f'[{i}/{total}] {fname}', 'warn')
            try:
                out_path, n_sessions = convert_bib(path, fmt)
                self._log(
                    f'  ✓  {n_sessions} sessão(ões) → {os.path.basename(out_path)}',
                    'success')
                ok += 1
            except Exception as exc:
                self._log(f'  ✗  Erro: {exc}', 'error')
                failed += 1
            self.root.after(0, self._advance_progress)

        self._log('')
        self._log(f'Concluído: {ok} convertido(s), {failed} com erro(s).',
                  'success' if failed == 0 else 'warn')
        self.root.after(0, lambda: self.convert_btn.config(state='normal'))

    def _advance_progress(self):
        self.progress.step(1)

    def _log(self, msg, tag=''):
        def _write():
            self.log.config(state='normal')
            self.log.insert('end', msg + '\n', tag)
            self.log.see('end')
            self.log.config(state='disabled')
        self.root.after(0, _write)


# ──────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────

if __name__ == '__main__':
    if HAS_DND:
        root = TkinterDnD.Tk()
    else:
        root = tk.Tk()

    app = BibConverterApp(root)
    root.mainloop()
