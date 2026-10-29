"""Junta as fontes (Receita + Google + sites) e gera a lista final de contatos."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd
from rapidfuzz import fuzz

from prospecta.google_places import LimiteAtingido, Places, resumir_lugar
from prospecta.receita import PORTE, BaseReceita
from prospecta.site_scan import ler_sites
from prospecta.util import (dominio_proprio, email_parece_do_contador, norm_bairro, norm_nome_empresa,
                  norm_texto, telefone)


@dataclass
class Busca:
    segmento: str
    cnaes: list[str]
    termos: list[str]
    uf: str
    cidade: str
    municipio_codigo: str | None = None   # código da Receita; None = sem base da Receita
    bairros: list[str] = field(default_factory=list)  # nomes normalizados; vazio = cidade toda
    tipo_google: str | None = None
    incluir_secundario: bool = False
    usar_google: bool = True
    varrer_bairros: bool = True
    max_bairros_varredura: int | None = 15  # None = todos os bairros
    buscar_individual: bool = False
    visitar_sites: bool = True


def _prog_padrao(msg, frac=None):
    print(msg)


# --------------------------------------------------------------------------- Google


def _consultas_google(b: Busca, bairros_cidade: list[str]) -> list[str]:
    local = f"{b.cidade} - {b.uf}"
    consultas = []
    if b.bairros:
        for bairro in b.bairros:
            consultas += [f"{t} {bairro.title()}, {local}" for t in b.termos]
    else:
        consultas += [f"{t} em {local}" for t in b.termos]
        if b.varrer_bairros:
            for bairro in bairros_cidade[: b.max_bairros_varredura or None]:
                consultas += [f"{t} {bairro.title()}, {local}" for t in b.termos]
    return list(dict.fromkeys(consultas))


def _na_area(lugar: dict, b: Busca) -> bool:
    end = norm_texto(lugar["endereco_google"])
    if norm_texto(b.cidade) not in end:
        return False
    if lugar["status_google"] == "CLOSED_PERMANENTLY":
        return False
    if b.bairros:
        bg = norm_bairro(lugar["bairro_google"])
        return bg in b.bairros
    return True


def _chaves_linha(linha) -> dict:
    nomes = [linha.get("nome_fantasia"), linha.get("razao_social")]
    nomes += [s.split(" (")[0] for s in str(linha.get("socios") or "").split("; ") if s]
    return {"nomes": [norm_nome_empresa(n) for n in nomes if n and str(n).strip()],
            "cep": str(linha.get("cep") or ""), "numero": str(linha.get("numero") or "").strip()}


def _pontuar(lugar: dict, linha) -> float:
    ch = linha if isinstance(linha, dict) and "nomes" in linha else _chaves_linha(linha)
    ng = norm_nome_empresa(lugar["nome_google"])
    nota_nome = max((fuzz.token_set_ratio(ng, n) for n in ch["nomes"]), default=0)
    cep_ok = bool(lugar["cep_google"]) and lugar["cep_google"] == ch["cep"]
    num_ok = bool(lugar["numero_google"]) and lugar["numero_google"] == ch["numero"]
    if nota_nome >= 88 or (nota_nome >= 70 and (cep_ok or num_ok)) or (cep_ok and num_ok and nota_nome >= 50):
        return nota_nome + 15 * cep_ok + 10 * num_ok
    return 0.0


def _casar(lugares: list[dict], df: pd.DataFrame) -> dict[str, int]:
    """google_id -> índice da linha da Receita (casamento 1 para 1, melhor nota primeiro)."""
    chaves = {idx: _chaves_linha(linha) for idx, linha in df.iterrows()}
    pares = []
    for lug in lugares:
        for idx, ch in chaves.items():
            s = _pontuar(lug, ch)
            if s > 0:
                pares.append((s, lug["google_id"], idx))
    pares.sort(reverse=True)
    usados_g, usados_r, res = set(), set(), {}
    for s, gid, idx in pares:
        if gid in usados_g or idx in usados_r:
            continue
        usados_g.add(gid)
        usados_r.add(idx)
        res[gid] = idx
    return res


# --------------------------------------------------------------------------- execução


def executar(b: Busca, base: BaseReceita | None, places: Places | None,
             progresso=_prog_padrao) -> tuple[pd.DataFrame, dict]:
    stats = {"receita": 0, "google_lugares": 0, "casados": 0, "so_google": 0,
             "chamadas_google": 0, "buscas_do_cache": 0, "aviso": ""}

    # 1) Receita ------------------------------------------------------------
    if base is not None and base.pronta() and b.municipio_codigo:
        progresso("Consultando a base da Receita", 0.02)
        df = base.consultar(b.cnaes, b.municipio_codigo, b.incluir_secundario)
        bairros_cidade = df["bairro_norm"].value_counts().index.tolist()
        bairros_cidade = [x for x in bairros_cidade if x != "(SEM BAIRRO)"]
        if b.bairros:
            df = df[df["bairro_norm"].isin(b.bairros)]
        df = df.reset_index(drop=True)
    else:
        df = pd.DataFrame()
        bairros_cidade = []
    stats["receita"] = len(df)

    # 2) Google: varredura ---------------------------------------------------
    lugares: dict[str, dict] = {}
    if b.usar_google and places is not None:
        consultas = _consultas_google(b, bairros_cidade)
        for i, q in enumerate(consultas):
            progresso(f"Google: {q}", 0.05 + 0.35 * i / max(1, len(consultas)))
            try:
                for p in places.buscar(q, b.tipo_google, ja_vistos=set(lugares)):
                    lug = resumir_lugar(p)
                    if lug["google_id"] and _na_area(lug, b):
                        lugares.setdefault(lug["google_id"], lug)
            except LimiteAtingido:
                stats["aviso"] = "O limite de chamadas ao Google foi atingido; a varredura ficou parcial."
                break
    stats["google_lugares"] = len(lugares)

    # 3) Casamento Google x Receita ------------------------------------------
    progresso("Cruzando Google com a Receita", 0.42)
    casamento = _casar(list(lugares.values()), df) if len(df) and lugares else {}
    google_por_linha = {idx: lugares[gid] for gid, idx in casamento.items()}

    # 4) Busca individual dos CNPJs que o Google ainda não achou ---------------
    if b.usar_google and places is not None and b.buscar_individual and len(df):
        faltam = [i for i in df.index if i not in google_por_linha]
        for n, idx in enumerate(faltam):
            if places.restantes <= 0:
                stats["aviso"] = "O limite de chamadas ao Google acabou antes da busca individual terminar."
                break
            l = df.loc[idx]
            nome = l["nome_fantasia"] or l["razao_social"]
            q = f"{nome}, {l['tipo_logradouro'] or ''} {l['logradouro'] or ''} {l['numero'] or ''}, {b.cidade} - {b.uf}"
            progresso(f"Google individual ({n + 1}/{len(faltam)}): {nome}", 0.45 + 0.2 * n / max(1, len(faltam)))
            try:
                achados = [resumir_lugar(p) for p in places.buscar(q, paginas=1)[:3]]
            except LimiteAtingido:
                break
            achados = [a for a in achados if a["google_id"] not in casamento]
            melhor = max(achados, key=lambda a: _pontuar(a, l), default=None)
            if melhor and _pontuar(melhor, l) > 0:
                casamento[melhor["google_id"]] = idx
                google_por_linha[idx] = melhor
                lugares.setdefault(melhor["google_id"], melhor)
    stats["casados"] = len(google_por_linha)
    stats["chamadas_google"] = places.chamadas if places else 0
    stats["buscas_do_cache"] = places.do_cache if places else 0

    # 5) Montar registros ----------------------------------------------------
    registros = []
    for idx, l in df.iterrows():
        registros.append({"_linha": l, "_google": google_por_linha.get(idx)})
    so_google = [g for gid, g in lugares.items() if gid not in casamento]
    stats["so_google"] = len(so_google)
    for g in so_google:
        registros.append({"_linha": None, "_google": g})

    # 6) Sites ---------------------------------------------------------------
    urls = {}
    for i, r in enumerate(registros):
        g, l = r["_google"], r["_linha"]
        site = (g or {}).get("site", "")
        r["_site_origem"] = "Google" if site else ""
        if not site and l is not None:
            dom = dominio_proprio(l.get("email") or "")
            if dom:
                site, r["_site_origem"] = f"https://{dom}", "domínio do e-mail"
        r["_site"] = site
        if site and b.visitar_sites:
            urls[str(i)] = site
    lidos = ler_sites(urls, progresso=lambda m, f: progresso(m, 0.65 + 0.3 * (f or 0))) if urls else {}

    progresso("Montando a planilha", 0.97)
    linhas = [_linha_final(r, lidos.get(str(i)), b) for i, r in enumerate(registros)]
    out = pd.DataFrame(linhas)
    if not out.empty:
        out = out.sort_values(["completude", "bairro"], ascending=[False, True]).reset_index(drop=True)
    progresso("Pronto!", 1.0)
    return out, stats


def _linha_final(r: dict, site_info: dict | None, b: Busca) -> dict:
    l, g = r["_linha"], r["_google"] or {}
    site_info = site_info or {}
    obs = []

    # Telefones -------------------------------------------------------------
    tels = []  # (origem, dict)
    if l is not None:
        for d, t in (("ddd_1", "telefone_1"), ("ddd_2", "telefone_2")):
            x = telefone(l.get(d), l.get(t))
            if x:
                tels.append(("receita", x))
    if g.get("telefone_google"):
        x = telefone(g["telefone_google"])
        if x:
            tels.append(("google", x))
    for e164 in sorted(site_info.get("telefones", [])):
        x = telefone(e164)
        if x:
            tels.append(("site", x))

    whats_conf = sorted(site_info.get("whatsapp", []))
    celulares = list(dict.fromkeys(x["e164"] for _, x in tels if x["tipo"] == "celular"))
    fixos = list(dict.fromkeys(x["formatado"] for _, x in tels if x["tipo"] == "fixo"))
    ordem = {"google": 0, "site": 1, "receita": 2}
    melhor_tel = sorted(tels, key=lambda t: (ordem[t[0]], t[1]["tipo"] != "fixo"))
    telefone_principal = melhor_tel[0][1]["formatado"] if melhor_tel else ""
    whatsapp = whats_conf[0] if whats_conf else (celulares[0] if celulares else "")
    whatsapp_status = "confirmado no site" if whats_conf else ("provável (celular)" if celulares else "")

    # E-mails ---------------------------------------------------------------
    emails_site = sorted(site_info.get("emails", []))
    email_rec = (l.get("email") or "").strip() if l is not None else ""
    if email_rec and email_parece_do_contador(email_rec):
        obs.append("e-mail da Receita parece ser do contador")
    dom_site = ""
    if r.get("_site"):
        dom_site = norm_texto(r["_site"]).replace("HTTPS ", "").replace("HTTP ", "").replace("WWW ", "")
    def chave_email(e):
        return (0 if dom_site and norm_texto(e.split("@")[-1]) in dom_site else 1,
                1 if email_parece_do_contador(e) else 0, e)
    todos_emails = sorted(set(emails_site + ([email_rec] if email_rec else [])), key=chave_email)

    if r.get("_site") and site_info and not site_info.get("site_ok"):
        if r.get("_site_origem") == "domínio do e-mail":
            r["_site"], r["_site_origem"] = "", ""  # era só um palpite e não abriu
        else:
            obs.append("site não abriu")

    # Endereço --------------------------------------------------------------
    if l is not None:
        end = " ".join(str(x) for x in [l.get("tipo_logradouro"), l.get("logradouro")] if x)
        end += f", {l.get('numero') or 's/n'}" + (f" - {l['complemento']}" if l.get("complemento") else "")
        bairro = l.get("bairro_norm") or ""
        cep = l.get("cep") or ""
        data = str(l.get("data_inicio_atividade") or "")
        abertura = f"{data[6:8]}/{data[4:6]}/{data[:4]}" if len(data) == 8 else ""
        cnpj = str(l.get("cnpj") or "")
        cnpj_fmt = f"{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:]}" if len(cnpj) == 14 else cnpj
    else:
        end, cep, abertura, cnpj_fmt = g.get("endereco_google", ""), g.get("cep_google", ""), "", ""
        bairro = norm_bairro(g.get("bairro_google")) if g.get("bairro_google") else ""
        obs.append("só no Google (sem CNPJ encontrado - pode ser advogado/profissional autônomo)")

    origem = "Receita + Google" if (l is not None and g) else ("Receita" if l is not None else "Google")
    socios = (l.get("socios") or "") if l is not None else ""

    completude = sum([bool(telefone_principal), bool(whatsapp), bool(todos_emails),
                      bool(r.get("_site")), bool(socios)])
    return {
        "origem": origem,
        "nome": (l.get("nome_fantasia") or l.get("razao_social") if l is not None else "") or g.get("nome_google", ""),
        "razao_social": l.get("razao_social", "") if l is not None else "",
        "nome_google": g.get("nome_google", ""),
        "cnpj": cnpj_fmt,
        "socios_responsaveis": socios,
        "telefone_principal": telefone_principal,
        "whatsapp": whatsapp,
        "whatsapp_status": whatsapp_status,
        "celulares": "; ".join(celulares),
        "telefones_fixos": "; ".join(fixos),
        "email_principal": todos_emails[0] if todos_emails else "",
        "emails": "; ".join(todos_emails),
        "site": r.get("_site", ""),
        "site_origem": r.get("_site_origem", ""),
        "instagram": site_info.get("instagram", ""),
        "facebook": site_info.get("facebook", ""),
        "linkedin": site_info.get("linkedin", ""),
        "endereco": end,
        "bairro": bairro,
        "cep": cep,
        "cidade": b.cidade,
        "uf": b.uf,
        "data_abertura": abertura,
        "porte": PORTE.get(str(l.get("porte") or ""), "") if l is not None else "",
        "nota_google": g.get("nota_google", ""),
        "avaliacoes_google": g.get("avaliacoes_google", ""),
        "link_maps": g.get("link_maps", ""),
        "completude": completude,
        "observacoes": "; ".join(obs),
        "segmento": b.segmento,
        "gerado_em": datetime.now().strftime("%d/%m/%Y %H:%M"),
    }
