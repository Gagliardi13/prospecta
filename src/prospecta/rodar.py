"""Inicia a tela do Prospecta e abre o navegador quando ela estiver pronta."""
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

PORTA = 8501
URL = f"http://localhost:{PORTA}"


def abrir_navegador():
    for _ in range(180):
        try:
            urllib.request.urlopen(f"{URL}/_stcore/health", timeout=1)
            webbrowser.open(URL)
            print(f"\n  Prospecta aberto em {URL}  (para fechar, feche esta janela)\n")
            return
        except Exception:
            time.sleep(1)


def main():
    from streamlit.web import cli

    threading.Thread(target=abrir_navegador, daemon=True).start()
    app = str(Path(__file__).with_name("app.py"))
    sys.argv = ["streamlit", "run", app, f"--server.port={PORTA}", "--server.headless=true",
                "--browser.gatherUsageStats=false", "--server.address=localhost"]
    sys.exit(cli.main())


if __name__ == "__main__":
    main()
