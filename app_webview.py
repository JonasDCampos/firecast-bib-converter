"""
Entry point para o .exe com janela nativa (flaskwebgui + Flask).
Abre o Edge/Chrome em modo app — sem barra de URL, sem abas.
"""

from flaskwebgui import FlaskUI
from server import app


if __name__ == '__main__':
    FlaskUI(
        app=app,
        server='flask',
        width=660,
        height=600,
        port=5000,
    ).run()
