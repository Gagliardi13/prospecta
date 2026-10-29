#!/usr/bin/env bash
# Instalador do Prospecta para Linux / macOS
# Uso:  curl -LsSf https://raw.githubusercontent.com/Gagliardi13/prospecta/main/instalar.sh | bash
set -e
REPO="${PROSPECTA_REPO:-Gagliardi13/prospecta}"
FONTE="${PROSPECTA_FONTE:-prospecta @ https://github.com/$REPO/archive/refs/heads/main.zip}"
PASTA="$HOME/Prospecta"

echo; echo "  Instalando o Prospecta..."; echo
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"

uv tool install --python 3.12 --force "$FONTE"

mkdir -p "$PASTA"
cat > "$PASTA/iniciar.sh" <<LAUNCH
#!/usr/bin/env bash
export PATH="\$HOME/.local/bin:\$PATH"
echo "  Procurando atualizações..."
uv tool install --quiet --python 3.12 --refresh-package prospecta --reinstall-package prospecta "$FONTE" 2>/dev/null \\
  || echo "  Sem internet: abrindo a versão instalada."
exec prospecta
LAUNCH
chmod +x "$PASTA/iniciar.sh"

if [ -d "$HOME/.local/share/applications" ] || [ "$(uname)" = "Linux" ]; then
  mkdir -p "$HOME/.local/share/applications"
  cat > "$HOME/.local/share/applications/prospecta.desktop" <<DESK
[Desktop Entry]
Name=Prospecta
Comment=Encontrar empresas por segmento e bairro
Exec=$PASTA/iniciar.sh
Terminal=true
Type=Application
Icon=system-search
DESK
fi

echo; echo "  Pronto! Para abrir: $PASTA/iniciar.sh  (ou procure 'Prospecta' no menu de aplicativos)"; echo
