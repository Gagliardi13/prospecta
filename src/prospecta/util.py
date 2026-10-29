"""Funções de normalização de texto, nomes, bairros e telefones."""
from __future__ import annotations

import re
import unicodedata

# --------------------------------------------------------------------------- texto


def sem_acento(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def norm_texto(s: object) -> str:
    """Maiúsculas, sem acento, sem pontuação, espaços únicos."""
    if s is None:
        return ""
    s = sem_acento(str(s)).upper()
    s = re.sub(r"[^A-Z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# Abreviações comuns no campo "bairro" da Receita
_ABREV_BAIRRO = {
    "JD": "JARDIM", "JDM": "JARDIM", "JARD": "JARDIM",
    "VL": "VILA", "V": "VILA",
    "PQ": "PARQUE", "PQE": "PARQUE", "PRQ": "PARQUE",
    "RES": "RESIDENCIAL", "CJ": "CONJUNTO", "CONJ": "CONJUNTO",
    "STA": "SANTA", "STO": "SANTO", "S": "SAO",
    "NSA": "NOSSA", "SRA": "SENHORA", "N": "NOSSA",
    "CH": "CHACARA", "BAIRRO": "",
}


def norm_bairro(s: object) -> str:
    t = norm_texto(s)
    if not t:
        return "(SEM BAIRRO)"
    partes = [_ABREV_BAIRRO.get(p, p) for p in t.split()]
    t = " ".join(p for p in partes if p)
    # "CENTRO SBC", "CENTRO SAO BERNARDO" -> "CENTRO"
    t = re.sub(r"^CENTRO\b.*", "CENTRO", t)
    return t or "(SEM BAIRRO)"


# Palavras que não ajudam a identificar uma empresa pelo nome
_GENERICAS = set(
    """
    DE DA DO DAS DOS E EM A O AS OS
    LTDA ME EPP EIRELI SS S SA SOCIEDADE SIMPLES INDIVIDUAL LIMITADA UNIPESSOAL CIA
    ADVOGADOS ADVOGADO ADVOGADA ADVOGADAS ADVOCACIA ADVOCATICIA ADVOCATICIOS ASSOCIADOS ASSOCIADAS
    ESCRITORIO CONSULTORIA ASSESSORIA SERVICOS SERVICO JURIDICO JURIDICA JURIDICOS
    CLINICA CLINICAS CONSULTORIO ODONTOLOGIA ODONTOLOGICA ODONTOLOGICO ODONTO DENTISTA DENTAL
    CENTRO INSTITUTO GRUPO DR DRA DRS PROF
    """.split()
)


def norm_nome_empresa(s: object) -> str:
    t = norm_texto(s)
    sobra = [p for p in t.split() if p not in _GENERICAS and not p.isdigit()]
    return " ".join(sobra) if sobra else t


# --------------------------------------------------------------------------- telefones

_ESPECIAIS = ("0800", "0300", "0500", "0900", "4003", "4004", "4020", "3003", "3004")


def telefone(ddd: object = None, numero: object = None) -> dict | None:
    """Normaliza um telefone brasileiro.

    Aceita (ddd, numero) separados (formato da Receita) ou só `ddd` com o número
    completo em texto livre ("(11) 4123-4567", "+55 11 98765-4321", "5511987654321").
    Retorna {"e164", "formatado", "tipo"} com tipo "celular", "fixo" ou "especial".
    """
    bruto = re.sub(r"\D", "", f"{ddd or ''}{numero or ''}")
    if not bruto:
        return None
    if bruto.startswith(_ESPECIAIS) or bruto.lstrip("0").startswith(_ESPECIAIS):
        return {"e164": "", "formatado": bruto, "tipo": "especial"}
    if len(bruto) >= 12 and bruto.startswith("55"):
        bruto = bruto[2:]
    bruto = bruto.lstrip("0")
    if len(bruto) not in (10, 11):
        return None
    dd, num = bruto[:2], bruto[2:]
    if not (11 <= int(dd) <= 99):
        return None
    if len(num) == 9:
        if num[0] != "9":
            return None
        tipo = "celular"
    else:  # 8 dígitos
        if num[0] in "6789":  # celular antigo, sem o 9 na frente
            num, tipo = "9" + num, "celular"
        elif num[0] in "2345":
            tipo = "fixo"
        else:
            return None
    fmt = f"({dd}) {num[:-4]}-{num[-4:]}"
    return {"e164": f"+55{dd}{num}", "formatado": fmt, "tipo": tipo}


# --------------------------------------------------------------------------- e-mails

PROVEDORES_GRATUITOS = {
    "gmail.com", "hotmail.com", "hotmail.com.br", "outlook.com", "outlook.com.br", "live.com",
    "msn.com", "yahoo.com", "yahoo.com.br", "ymail.com", "uol.com.br", "bol.com.br",
    "terra.com.br", "ig.com.br", "icloud.com", "me.com", "globo.com", "globomail.com",
    "r7.com", "aol.com", "protonmail.com", "proton.me", "zipmail.com.br", "oi.com.br",
}

_RE_CONTADOR = re.compile(r"contab|contador|contabil|fiscal|dp\.|escrita|assessoriacont", re.I)


def email_parece_do_contador(email: str) -> bool:
    return bool(_RE_CONTADOR.search(email or ""))


def dominio_proprio(email: str) -> str | None:
    """Domínio do e-mail se não for provedor gratuito nem parecer de contador."""
    if not email or "@" not in email or email_parece_do_contador(email):
        return None
    dom = email.split("@", 1)[1].strip().lower()
    if dom in PROVEDORES_GRATUITOS or "." not in dom:
        return None
    return dom
