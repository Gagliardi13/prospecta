"""Google Places API (New) - Text Search, com contador de chamadas e limite de gasto."""
from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

URL = "https://places.googleapis.com/v1/places:searchText"

# Telefone, site e nota fazem a chamada ser cobrada na SKU "Text Search Enterprise"
# (1.000 chamadas grátis por mês; depois ~US$ 35 a cada 1.000 - confira a tabela do Google).
CAMPOS = ",".join([
    "places.id", "places.displayName", "places.formattedAddress", "places.businessStatus",
    "places.nationalPhoneNumber", "places.internationalPhoneNumber", "places.websiteUri",
    "places.rating", "places.userRatingCount", "places.googleMapsUri", "nextPageToken",
])
COTA_GRATIS_MES = 1000
PRECO_POR_MIL_USD = 35.0


class ErroGoogle(RuntimeError):
    pass


class LimiteAtingido(RuntimeError):
    pass


class ContadorUso:
    """Guarda quantas chamadas este programa fez no mês (para não estourar a cota grátis)."""

    def __init__(self, arquivo: Path):
        self.arquivo = Path(arquivo)

    def _ler(self) -> dict:
        try:
            return json.loads(self.arquivo.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def usados_no_mes(self) -> int:
        return int(self._ler().get(date.today().strftime("%Y-%m"), 0))

    def somar(self, n: int = 1) -> None:
        d = self._ler()
        mes = date.today().strftime("%Y-%m")
        d[mes] = int(d.get(mes, 0)) + n
        self.arquivo.parent.mkdir(parents=True, exist_ok=True)
        self.arquivo.write_text(json.dumps(d, indent=2), encoding="utf-8")


CACHE_DIAS = 30  # os termos do Google permitem guardar resultados por até 30 dias


class CacheBuscas:
    """Guarda o resultado de cada busca por 30 dias: repetir a mesma busca não gasta cota."""

    def __init__(self, pasta: Path):
        self.pasta = Path(pasta)

    def _arq(self, texto: str, tipo: str | None) -> Path:
        h = hashlib.sha1(f"{texto.strip().lower()}|{tipo or ''}".encode()).hexdigest()[:20]
        return self.pasta / f"{h}.json"

    def ler(self, texto: str, tipo: str | None) -> list[dict] | None:
        a = self._arq(texto, tipo)
        try:
            d = json.loads(a.read_text(encoding="utf-8"))
            if datetime.fromisoformat(d["quando"]) > datetime.now() - timedelta(days=CACHE_DIAS):
                return d["lugares"]
        except Exception:
            pass
        return None

    def gravar(self, texto: str, tipo: str | None, lugares: list[dict]) -> None:
        self.pasta.mkdir(parents=True, exist_ok=True)
        self._arq(texto, tipo).write_text(json.dumps(
            {"quando": datetime.now().isoformat(), "texto": texto, "lugares": lugares},
            ensure_ascii=False), encoding="utf-8")

    def limpar_vencidos(self) -> None:
        for a in self.pasta.glob("*.json") if self.pasta.exists() else []:
            try:
                d = json.loads(a.read_text(encoding="utf-8"))
                if datetime.fromisoformat(d["quando"]) <= datetime.now() - timedelta(days=CACHE_DIAS):
                    a.unlink()
            except Exception:
                a.unlink(missing_ok=True)


class Places:
    def __init__(self, chave: str, limite_chamadas: int, contador: ContadorUso | None = None,
                 sessao: requests.Session | None = None, cache: CacheBuscas | None = None):
        self.chave = chave.strip()
        self.limite = max(0, int(limite_chamadas))
        self.contador = contador
        self.sessao = sessao or requests.Session()
        self.cache = cache
        self.chamadas = 0
        self.do_cache = 0

    @property
    def restantes(self) -> int:
        return max(0, self.limite - self.chamadas)

    def _post(self, corpo: dict) -> dict:
        if self.chamadas >= self.limite:
            raise LimiteAtingido("Limite de chamadas ao Google desta busca foi atingido.")
        for tentativa in range(3):
            r = self.sessao.post(URL, json=corpo, timeout=30, headers={
                "Content-Type": "application/json",
                "X-Goog-Api-Key": self.chave,
                "X-Goog-FieldMask": CAMPOS,
            })
            self.chamadas += 1
            if self.contador:
                self.contador.somar()
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 500, 503) and tentativa < 2:
                time.sleep(2 * (tentativa + 1))
                continue
            if r.status_code == 400 and "pageToken" in corpo and tentativa < 2:
                time.sleep(2)  # token de página às vezes demora a ficar válido
                continue
            raise ErroGoogle(self._explicar_erro(r))
        raise ErroGoogle("O Google não respondeu depois de 3 tentativas.")

    @staticmethod
    def _explicar_erro(r: requests.Response) -> str:
        try:
            msg = r.json().get("error", {}).get("message", r.text[:300])
        except Exception:
            msg = r.text[:300]
        dicas = {
            400: "Pedido inválido.",
            401: "Chave de API inválida.",
            403: "Chave sem permissão: confira se a 'Places API (New)' está ativada no projeto "
                 "e se a conta de faturamento está vinculada.",
            429: "Muitas chamadas seguidas ou cota diária atingida.",
        }
        return f"Google respondeu {r.status_code}. {dicas.get(r.status_code, '')} Detalhe: {msg}"

    def buscar(self, texto: str, tipo: str | None = None, paginas: int = 3,
               ja_vistos: set[str] | None = None) -> list[dict]:
        """Text Search com até `paginas` páginas de 20 resultados (máximo do Google: 60).

        Se `ja_vistos` for informado, para de pedir páginas quando quase tudo que veio
        já tinha sido encontrado em buscas anteriores (economiza chamadas).
        """
        if self.cache:
            em_cache = self.cache.ler(texto, tipo)
            if em_cache is not None:
                self.do_cache += 1
                return em_cache
        corpo = {"textQuery": texto, "languageCode": "pt-BR", "regionCode": "BR", "pageSize": 20}
        if tipo:
            corpo["includedType"] = tipo
        lugares: list[dict] = []
        completo = True
        for _ in range(paginas):
            try:
                d = self._post(corpo)
            except LimiteAtingido:
                if lugares:
                    completo = False
                    break
                raise
            pagina = d.get("places", [])
            lugares.extend(pagina)
            token = d.get("nextPageToken")
            if not token:
                break
            if ja_vistos is not None and pagina:
                repetidos = sum(1 for p in pagina if p.get("id") in ja_vistos)
                if repetidos / len(pagina) >= 0.8:
                    break  # a próxima página provavelmente só traz repetidos
            corpo = {**corpo, "pageToken": token}
        if self.cache and completo:
            self.cache.gravar(texto, tipo, lugares)
        return lugares


def resumir_lugar(p: dict) -> dict:
    """Converte a resposta do Google num dicionário simples."""
    end = p.get("formattedAddress", "") or ""
    cep = re.search(r"\b(\d{5})-?(\d{3})\b", end)
    numero = re.search(r",\s*(\d+)\b", end)
    # Formato BR: "R. X, 123 - Bairro, Cidade - UF, 00000-000, Brasil"
    bairro = re.search(r"\d[^,]*?\s-\s([^,]+),", end) or re.search(r"\s-\s([^,]+),\s*[^,]+\s-\s[A-Z]{2}\b", end)
    return {
        "google_id": p.get("id", ""),
        "nome_google": (p.get("displayName") or {}).get("text", ""),
        "endereco_google": end,
        "cep_google": (cep.group(1) + cep.group(2)) if cep else "",
        "numero_google": numero.group(1) if numero else "",
        "bairro_google": bairro.group(1).strip() if bairro else "",
        "telefone_google": p.get("nationalPhoneNumber") or p.get("internationalPhoneNumber") or "",
        "site": p.get("websiteUri", "") or "",
        "nota_google": p.get("rating", ""),
        "avaliacoes_google": p.get("userRatingCount", ""),
        "status_google": p.get("businessStatus", ""),
        "link_maps": p.get("googleMapsUri", ""),
    }
