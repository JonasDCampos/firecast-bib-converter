"""
Firecast BIB → HTML  –  Web interface
Run:  python server.py
Then open http://localhost:5000
"""

from flask import Flask, request, render_template_string, send_file, jsonify
import io
import os

# Reuse all parsing logic from bib_converter
from bib_converter import convert_bib_to_html

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50 MB upload limit

# ── HTML template ─────────────────────────────────────────────────────────────

PAGE = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Firecast BIB → HTML</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: #1a1a2e;
      color: #e0e0e0;
      font-family: Georgia, 'Times New Roman', serif;
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      align-items: center;
      padding: 48px 20px;
    }
    h1 {
      color: #c9a84c;
      font-size: 2rem;
      letter-spacing: 3px;
      margin-bottom: 8px;
    }
    .subtitle {
      color: #555;
      font-size: .9rem;
      margin-bottom: 48px;
      font-family: 'Segoe UI', sans-serif;
    }
    .card {
      background: #16213e;
      border-radius: 10px;
      padding: 36px 40px;
      width: 100%;
      max-width: 560px;
      border: 1px solid #1e2a4a;
    }
    .drop-zone {
      border: 2px dashed #2a2a4a;
      border-radius: 8px;
      padding: 40px 20px;
      text-align: center;
      cursor: pointer;
      transition: border-color .2s, background .2s;
      background: #0d1117;
      position: relative;
    }
    .drop-zone:hover, .drop-zone.drag-over {
      border-color: #c9a84c;
      background: #1a2236;
    }
    .drop-zone input[type=file] {
      position: absolute; inset: 0;
      opacity: 0; cursor: pointer; width: 100%; height: 100%;
    }
    .drop-icon { font-size: 2.4rem; margin-bottom: 10px; }
    .drop-label {
      color: #888;
      font-family: 'Segoe UI', sans-serif;
      font-size: .9rem;
    }
    .drop-label strong { color: #c9a84c; }
    #file-name {
      margin-top: 10px;
      font-size: .85rem;
      color: #4caf7d;
      font-family: 'Consolas', monospace;
      min-height: 1.2em;
    }
    .btn {
      display: block;
      width: 100%;
      margin-top: 20px;
      padding: 12px;
      background: #0f3460;
      color: #e0e0e0;
      border: none;
      border-radius: 6px;
      font-size: 1rem;
      font-family: 'Segoe UI', sans-serif;
      font-weight: bold;
      cursor: pointer;
      letter-spacing: 1px;
      transition: background .2s;
    }
    .btn:hover { background: #1a5276; color: #c9a84c; }
    .btn:disabled { background: #1a1a2e; color: #444; cursor: not-allowed; }
    #status {
      margin-top: 18px;
      font-family: 'Consolas', monospace;
      font-size: .85rem;
      min-height: 1.4em;
      text-align: center;
    }
    .status-ok  { color: #4caf7d; }
    .status-err { color: #e05c5c; }
    .status-inf { color: #7ec8e3; }
    .spinner {
      display: inline-block;
      width: 14px; height: 14px;
      border: 2px solid #c9a84c44;
      border-top-color: #c9a84c;
      border-radius: 50%;
      animation: spin .7s linear infinite;
      vertical-align: middle;
      margin-right: 6px;
    }
    @keyframes spin { to { transform: rotate(360deg); } }
    footer {
      margin-top: 40px;
      color: #333;
      font-size: .75rem;
      font-family: 'Segoe UI', sans-serif;
    }
  </style>
</head>
<body>
  <h1>Firecast BIB → HTML</h1>
  <p class="subtitle">Conversor de sessões RRPG</p>

  <div class="card">
    <form id="form" enctype="multipart/form-data">
      <div class="drop-zone" id="drop-zone">
        <div class="drop-icon">📜</div>
        <div class="drop-label">
          <strong>Clique ou arraste</strong> um arquivo <strong>.bib</strong> aqui
        </div>
        <div id="file-name"></div>
        <input type="file" name="file" id="file-input" accept=".bib">
      </div>
      <button class="btn" type="submit" id="btn" disabled>⚙ Converter</button>
    </form>
    <div id="status"></div>
  </div>

  <footer>Firecast BIB Converter • interface web</footer>

  <script>
    const input    = document.getElementById('file-input');
    const btn      = document.getElementById('btn');
    const status   = document.getElementById('status');
    const fileName = document.getElementById('file-name');
    const dropZone = document.getElementById('drop-zone');

    input.addEventListener('change', () => {
      if (input.files.length) {
        fileName.textContent = input.files[0].name;
        btn.disabled = false;
        status.textContent = '';
      }
    });

    dropZone.addEventListener('dragover',  e => { e.preventDefault(); dropZone.classList.add('drag-over'); });
    dropZone.addEventListener('dragleave', () => dropZone.classList.remove('drag-over'));
    dropZone.addEventListener('drop', e => {
      e.preventDefault();
      dropZone.classList.remove('drag-over');
      const f = e.dataTransfer.files[0];
      if (f) {
        // Manually assign to file input via DataTransfer
        const dt = new DataTransfer();
        dt.items.add(f);
        input.files = dt.files;
        fileName.textContent = f.name;
        btn.disabled = false;
        status.textContent = '';
      }
    });

    document.getElementById('form').addEventListener('submit', async e => {
      e.preventDefault();
      if (!input.files.length) return;

      btn.disabled = true;
      status.innerHTML = '<span class="spinner"></span><span class="status-inf">Convertendo…</span>';

      const data = new FormData();
      data.append('file', input.files[0]);

      try {
        const res = await fetch('/convert', { method: 'POST', body: data });
        if (!res.ok) {
          const err = await res.json();
          status.innerHTML = `<span class="status-err">✗ Erro: ${err.error}</span>`;
          btn.disabled = false;
          return;
        }
        // Trigger download
        const blob = await res.blob();
        const url  = URL.createObjectURL(blob);
        const a    = document.createElement('a');
        const orig = input.files[0].name.replace(/\\.bib$/i, '.html');
        a.href = url; a.download = orig; a.click();
        URL.revokeObjectURL(url);
        status.innerHTML = `<span class="status-ok">✓ ${orig} pronto — download iniciado!</span>`;
      } catch (err) {
        status.innerHTML = `<span class="status-err">✗ ${err.message}</span>`;
      }
      btn.disabled = false;
    });
  </script>
</body>
</html>
"""

# ── Routes ────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template_string(PAGE)


@app.route('/convert', methods=['POST'])
def convert():
    if 'file' not in request.files:
        return jsonify(error='Nenhum arquivo enviado.'), 400

    f = request.files['file']
    if not f.filename:
        return jsonify(error='Nome de arquivo vazio.'), 400
    if not f.filename.lower().endswith('.bib'):
        return jsonify(error='Apenas arquivos .bib são aceitos.'), 400

    # Save to a temp file, convert, return HTML
    import tempfile, shutil
    tmp_dir = tempfile.mkdtemp()
    try:
        bib_path  = os.path.join(tmp_dir, f.filename)
        html_path = os.path.splitext(bib_path)[0] + '.html'

        f.save(bib_path)
        convert_bib_to_html(bib_path)   # writes html_path

        with open(html_path, 'rb') as fh:
            data = fh.read()

        out_name = os.path.basename(html_path)
        return send_file(
            io.BytesIO(data),
            mimetype='text/html; charset=utf-8',
            as_attachment=True,
            download_name=out_name,
        )
    except Exception as exc:
        return jsonify(error=str(exc)), 500
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
