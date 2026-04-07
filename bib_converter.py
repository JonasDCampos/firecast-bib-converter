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
import threading
from html import escape

SEPARATOR = b'\x96\xea\x02\x00\x00\x00\x00\x00\x96\xea\x95\x00'
HEADER_SIZE = 21


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


def find_sessions(data):
    """Locate all session blocks in the decompressed data.

    Returns list of (name: str, xml_bytes: bytes).
    """
    sessions = []

    sep_positions = []
    pos = 0
    while True:
        p = data.find(SEPARATOR, pos)
        if p == -1:
            break
        sep_positions.append(p)
        pos = p + 1

    for idx, sep_pos in enumerate(sep_positions):
        # Name: bytes between the last \x00 and the separator
        null_pos = -1
        for i in range(sep_pos - 1, max(sep_pos - 256, -1), -1):
            if data[i] == 0:
                null_pos = i
                break
        name_start = null_pos + 1 if null_pos >= 0 else 0
        try:
            name = data[name_start:sep_pos].decode('utf-8', errors='replace').strip()
        except Exception:
            name = f'Sessão {idx + 1}'
        if not name:
            name = f'Sessão {idx + 1}'

        # 4-byte little-endian size after separator
        after_sep = sep_pos + len(SEPARATOR)
        if after_sep + 4 > len(data):
            break
        size = int.from_bytes(data[after_sep:after_sep + 4], 'little')
        xml_start = after_sep + 4

        # Upper bound = start of next session's name (or end of data)
        max_end = len(data)
        if idx + 1 < len(sep_positions):
            next_sep = sep_positions[idx + 1]
            for i in range(next_sep - 1, max(next_sep - 256, -1), -1):
                if data[i] == 0:
                    max_end = i
                    break
            else:
                max_end = next_sep

        if size > 0 and xml_start + size <= max_end:
            xml_data = data[xml_start:xml_start + size]
        else:
            xml_data = data[xml_start:max_end]

        xml_data = xml_data.rstrip(b'\x00')
        if xml_data:
            sessions.append((name, xml_data))

    return sessions


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


def convert_bib_to_html(bib_path):
    """Read → decompress → parse → write HTML. Returns (html_path, session_count)."""
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

    decompressed = zlib.decompress(search_area[zlib_pos:])
    sessions = find_sessions(decompressed)
    if not sessions:
        raise ValueError("Nenhuma sessão encontrada após a descompressão.")

    title = os.path.splitext(os.path.basename(bib_path))[0]
    parts = [HTML_HEAD.format(title=escape(title))]

    for idx, (name, xml_bytes) in enumerate(sessions):
        if idx > 0:
            parts.append('<hr class="divider">')
        parts.append(f'<div class="session-header">{escape(name)}</div>')
        parts.append('<div class="session-body">')

        paragraphs = parse_xml_session(xml_bytes)
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

    return html_path, len(sessions)


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
        self.root.title('Firecast BIB → HTML Converter')
        self.root.configure(bg=DARK_BG)
        self.root.minsize(620, 520)
        self.root.resizable(True, True)

        self.selected_files = []
        self._build_ui()

        if HAS_DND:
            self._setup_dnd()

    # ── UI ───────────────────────────────────────────────────────────────

    def _build_ui(self):
        # Top bar
        top = tk.Frame(self.root, bg=PANEL_BG, pady=14)
        top.pack(fill='x')

        tk.Label(top, text='Firecast  BIB → HTML',
                 bg=PANEL_BG, fg=ACCENT,
                 font=('Georgia', 16, 'bold')).pack()
        tk.Label(top, text='Converte arquivos de sessão do RRPG em HTML legível',
                 bg=PANEL_BG, fg='#888',
                 font=('Georgia', 9)).pack(pady=(2, 0))

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

        self._log(f'Iniciando conversão de {total} arquivo(s)…', 'info')

        for i, path in enumerate(self.selected_files, 1):
            fname = os.path.basename(path)
            self._log(f'[{i}/{total}] {fname}', 'warn')
            try:
                html_path, n_sessions = convert_bib_to_html(path)
                self._log(
                    f'  ✓  {n_sessions} sessão(ões) → {os.path.basename(html_path)}',
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
