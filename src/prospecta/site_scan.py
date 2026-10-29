"""Visita o site público da empresa e extrai e-mails, WhatsApp, telefones e redes sociais.

Lê só a página inicial e até 3 páginas de contato/sobre, respeita o robots.txt
e identifica o programa no User-Agent.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

from prospecta.util import telefone

UA = "Mozilla/5.0 (compatible; Prospecta/1.0; leitura de pagina de contato)"
TIMEOUT = 12
MAX_PAGINAS_EXTRA = 3

RE_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
RE_LINK_CONTATO = re.compile(r"contato|contact|fale|atendimento|sobre|quem-somos|quemsomos|escritorio|localiza", re.I)
_EMAIL_LIXO = re.compile(
    r"\.(png|jpe?g|gif|webp|svg|css|js|ico)$|example\.|exemplo\.|seuemail|seu-email|email@|"
    r"sentry|wixpress|godaddy|domain\.com|@2x|@3x|nome@|usuario@|user@", re.I)


def _wa_numero(href: str) -> str | None:
    u = urlparse(href)
    host = (u.netloc or "").lower()
    if "wa.me" in host:
        num = u.path.strip("/").split("/")[0]
    elif "whatsapp.com" in host:
        num = (parse_qs(u.query).get("phone") or [""])[0]
    else:
        return None
    t = telefone(num)
    return t["e164"] if t and t["tipo"] == "celular" else None


class LeitorSite:
    def __init__(self):
        self.sessao = requests.Session()
        self.sessao.headers["User-Agent"] = UA
        self._robots: dict[str, RobotFileParser | None] = {}

    def _permitido(self, url: str) -> bool:
        u = urlparse(url)
        base = f"{u.scheme}://{u.netloc}"
        if base not in self._robots:
            rp = RobotFileParser()
            try:
                r = self.sessao.get(base + "/robots.txt", timeout=8)
                rp.parse(r.text.splitlines() if r.status_code == 200 else [])
            except Exception:
                rp.parse([])
            self._robots[base] = rp
        rp = self._robots[base]
        return rp is None or rp.can_fetch(UA, url)

    def _baixar(self, url: str) -> tuple[str, str] | None:
        if not self._permitido(url):
            return None
        try:
            r = self.sessao.get(url, timeout=TIMEOUT, allow_redirects=True)
        except requests.RequestException:
            return None
        if r.status_code != 200 or "html" not in r.headers.get("Content-Type", "html").lower():
            return None
        r.encoding = r.encoding or r.apparent_encoding
        return r.url, r.text[:2_000_000]

    def ler(self, url: str) -> dict:
        res = {"emails": set(), "whatsapp": set(), "telefones": set(),
               "instagram": "", "facebook": "", "linkedin": "", "site_ok": False}
        if not url:
            return res
        if not url.startswith("http"):
            url = "https://" + url
        primeira = self._baixar(url)
        if primeira is None and url.startswith("https://"):
            primeira = self._baixar("http://" + url[8:])
        if primeira is None:
            return res
        res["site_ok"] = True
        url_final, html = primeira
        dominio = urlparse(url_final).netloc
        extras = self._extrair(html, url_final, res)
        vistos = {url_final}
        for link in [l for l in extras if urlparse(l).netloc == dominio][:MAX_PAGINAS_EXTRA]:
            if link in vistos:
                continue
            vistos.add(link)
            pag = self._baixar(link)
            if pag:
                self._extrair(pag[1], pag[0], res)
        return res

    def _extrair(self, html: str, url: str, res: dict) -> list[str]:
        sopa = BeautifulSoup(html, "html.parser")
        candidatos: list[str] = []
        for a in sopa.find_all("a", href=True):
            href = a["href"].strip()
            low = href.lower()
            if low.startswith("mailto:"):
                em = low[7:].split("?")[0].strip()
                if RE_EMAIL.fullmatch(em):
                    res["emails"].add(em)
            elif low.startswith("tel:"):
                t = telefone(href[4:])
                if t and t["e164"]:
                    res["telefones"].add(t["e164"])
            elif "wa.me" in low or "whatsapp.com" in low:
                n = _wa_numero(href)
                if n:
                    res["whatsapp"].add(n)
            elif "instagram.com/" in low and not res["instagram"]:
                res["instagram"] = href.split("?")[0]
            elif "facebook.com/" in low and not res["facebook"] and "sharer" not in low:
                res["facebook"] = href.split("?")[0]
            elif "linkedin.com/" in low and not res["linkedin"] and "share" not in low:
                res["linkedin"] = href.split("?")[0]
            elif RE_LINK_CONTATO.search(low) or RE_LINK_CONTATO.search(a.get_text(" ", strip=True)):
                candidatos.append(urljoin(url, href).split("#")[0])
        texto = sopa.get_text(" ", strip=True)
        for em in RE_EMAIL.findall(texto):
            em = em.lower().strip(".")
            if not _EMAIL_LIXO.search(em):
                res["emails"].add(em)
        res["emails"] = {e for e in res["emails"] if not _EMAIL_LIXO.search(e)}
        return candidatos


def ler_sites(urls: dict[str, str], paralelos: int = 8, progresso=None) -> dict[str, dict]:
    """urls: {chave: url} -> {chave: resultado}"""
    leitor = LeitorSite()
    itens = list(urls.items())
    saida: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=paralelos) as ex:
        futuros = {ex.submit(leitor.ler, u): k for k, u in itens}
        for i, fut in enumerate(futuros):
            k = futuros[fut]
            try:
                saida[k] = fut.result()
            except Exception:
                saida[k] = {"emails": set(), "whatsapp": set(), "telefones": set(),
                            "instagram": "", "facebook": "", "linkedin": "", "site_ok": False}
            if progresso:
                progresso(f"Lendo sites ({i + 1}/{len(itens)})", (i + 1) / max(1, len(itens)))
    return saida
