"""Base de CNPJ da Receita Federal: download, importação para Parquet e consultas.

Fluxo:
  1. baixar()   -> baixa os .zip do mês mais recente (retoma downloads interrompidos)
  2. importar() -> converte cada .zip em Parquet (compactado e rápido de consultar)
  3. consultar() / listar_bairros() / buscar_cnae() -> consultas locais, em segundos
"""
from __future__ import annotations

import json
import re
import shutil
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Callable

import duckdb
import pandas as pd
import requests

from prospecta.util import norm_bairro

BASE_URL = "https://arquivos.receitafederal.gov.br/dados/cnpj/dados_abertos_cnpj/"
HEADERS = {"User-Agent": "Mozilla/5.0 (Prospecta - uso pessoal)"}

COLUNAS = {
    "estabelecimentos": [
        "cnpj_basico", "cnpj_ordem", "cnpj_dv", "matriz_filial", "nome_fantasia",
        "situacao_cadastral", "data_situacao_cadastral", "motivo_situacao_cadastral",
        "nome_cidade_exterior", "pais", "data_inicio_atividade", "cnae_fiscal_principal",
        "cnae_fiscal_secundaria", "tipo_logradouro", "logradouro", "numero", "complemento",
        "bairro", "cep", "uf", "municipio", "ddd_1", "telefone_1", "ddd_2", "telefone_2",
        "ddd_fax", "fax", "correio_eletronico", "situacao_especial", "data_situacao_especial",
    ],
    "empresas": [
        "cnpj_basico", "razao_social", "natureza_juridica", "qualificacao_responsavel",
        "capital_social", "porte", "ente_federativo",
    ],
    "socios": [
        "cnpj_basico", "identificador_socio", "nome_socio", "cpf_cnpj_socio",
        "qualificacao_socio", "data_entrada_sociedade", "pais", "representante_legal",
        "nome_representante", "qualificacao_representante", "faixa_etaria",
    ],
    "municipios": ["codigo", "descricao"],
    "cnaes": ["codigo", "descricao"],
    "qualificacoes": ["codigo", "descricao"],
}
PREFIXO_ZIP = {
    "estabelecimentos": "estabelecimentos", "empresas": "empresas", "socios": "socios",
    "municipios": "municipios", "cnaes": "cnaes", "qualificacoes": "qualificacoes",
}
# Colunas de Estabelecimentos que não usamos (economiza espaço)
_ESTAB_DESCARTAR = {"nome_cidade_exterior", "pais", "ddd_fax", "fax", "situacao_especial",
                    "data_situacao_especial", "motivo_situacao_cadastral"}

PORTE = {"00": "Não informado", "01": "Microempresa", "03": "Pequeno porte", "05": "Demais"}
SITUACAO = {"01": "Nula", "02": "Ativa", "03": "Suspensa", "04": "Inapta", "08": "Baixada"}

Progresso = Callable[[str, float | None], None]


def _nada(msg: str, frac: float | None = None) -> None:  # progresso padrão
    print(msg)


class BaseReceita:
    def __init__(self, pasta: Path, url_base: str = BASE_URL):
        self.pasta = Path(pasta)
        self.url_base = (url_base or BASE_URL).rstrip("/") + "/"
        self.dir_zips = self.pasta / "zips"
        self.dir_parquet = self.pasta / "parquet"
        self.arq_meta = self.pasta / "base.json"

    # ------------------------------------------------------------------ estado
    def pronta(self) -> bool:
        return self.arq_meta.exists() and (self.dir_parquet / "estabelecimentos").exists()

    def info(self) -> dict:
        if not self.arq_meta.exists():
            return {}
        return json.loads(self.arq_meta.read_text(encoding="utf-8"))

    def _pq(self, tabela: str) -> str:
        p = self.dir_parquet / tabela
        return str(p / "*.parquet") if p.is_dir() else str(self.dir_parquet / f"{tabela}.parquet")

    def _con(self) -> duckdb.DuckDBPyConnection:
        con = duckdb.connect()
        con.execute("SET preserve_insertion_order = false")
        return con

    # ------------------------------------------------------------------ download
    def listar_meses(self) -> list[str]:
        r = requests.get(self.url_base, headers=HEADERS, timeout=60)
        r.raise_for_status()
        return sorted(set(re.findall(r'href="(\d{4}-\d{2})/?"', r.text)))

    def _url_mes(self, mes: str) -> str:
        # o endereço configurado pode já ser a pasta de um mês (".../2026-10/")
        return self.url_base if self.url_base.rstrip("/").endswith(mes) else f"{self.url_base}{mes}/"

    def listar_zips(self, mes: str) -> list[str]:
        r = requests.get(self._url_mes(mes), headers=HEADERS, timeout=60)
        r.raise_for_status()
        nomes = sorted(set(re.findall(r'href="([^"/?]+\.zip)"', r.text, re.I)))
        return [n for n in nomes if any(n.lower().startswith(p) for p in PREFIXO_ZIP.values())]

    def baixar(self, mes: str | None = None, progresso: Progresso = _nada) -> Path:
        m = re.search(r"(\d{4}-\d{2})/?$", self.url_base)
        if mes is None and m:
            mes = m.group(1)
        if mes is None:
            try:
                meses = self.listar_meses()
            except requests.RequestException as e:
                raise RuntimeError(f"Não consegui acessar {self.url_base} ({e}). Baixe os .zip manualmente "
                                   "e use 'Importar da pasta'.") from e
            if not meses:
                raise RuntimeError(
                    "Não encontrei os meses no site da Receita. O endereço pode ter mudado: "
                    "baixe os .zip manualmente e coloque-os na pasta 'dados/receita/zips/manual'."
                )
            mes = meses[-1]
        destino = self.dir_zips / mes
        destino.mkdir(parents=True, exist_ok=True)
        zips = self.listar_zips(mes)
        if not zips:
            raise RuntimeError(f"Nenhum arquivo .zip encontrado em {self._url_mes(mes)}. O site da Receita "
                               "pode ter mudado: baixe os .zip manualmente e use 'Importar da pasta'.")
        for i, nome in enumerate(zips):
            self._baixar_arquivo(f"{self._url_mes(mes)}{nome}", destino / nome,
                                 lambda f, n=nome, i=i: progresso(
                                     f"Baixando {nome} ({i + 1}/{len(zips)})", (i + f) / len(zips)))
        (destino / ".completo").write_text(mes)
        return destino

    @staticmethod
    def _baixar_arquivo(url: str, arq: Path, prog: Callable[[float], None]) -> None:
        parcial = arq.with_suffix(arq.suffix + ".parcial")
        if arq.exists():
            prog(1.0)
            return
        ja = parcial.stat().st_size if parcial.exists() else 0
        h = dict(HEADERS)
        if ja:
            h["Range"] = f"bytes={ja}-"
        with requests.get(url, headers=h, stream=True, timeout=120) as r:
            if r.status_code == 416:  # já estava completo
                parcial.rename(arq)
                return
            r.raise_for_status()
            if ja and r.status_code != 206:  # servidor ignorou o Range: recomeça
                ja = 0
            total = int(r.headers.get("Content-Length", 0)) + ja
            with open(parcial, "ab" if ja else "wb") as f:
                feito = ja
                for bloco in r.iter_content(1 << 20):
                    f.write(bloco)
                    feito += len(bloco)
                    if total:
                        prog(feito / total)
        parcial.rename(arq)

    # ------------------------------------------------------------------ importação
    def importar(self, pasta_zips: Path, ufs: list[str] | None = None,
                 somente_ativas: bool = True, apagar_zips: bool = False,
                 progresso: Progresso = _nada) -> None:
        pasta_zips = Path(pasta_zips)
        todos = sorted(pasta_zips.glob("*.zip"), key=lambda p: p.name.lower())
        por_tabela = {t: [z for z in todos if z.name.lower().startswith(p)]
                      for t, p in PREFIXO_ZIP.items()}
        faltando = [t for t, zs in por_tabela.items() if not zs]
        if faltando:
            raise RuntimeError(f"Faltam arquivos na pasta {pasta_zips}: {', '.join(faltando)}")

        novo = self.pasta / "parquet_novo"
        shutil.rmtree(novo, ignore_errors=True)
        novo.mkdir(parents=True)
        total = sum(len(z) for z in por_tabela.values())
        feito = 0
        con = self._con()
        try:
            for tabela, zips in por_tabela.items():
                for n, z in enumerate(zips):
                    progresso(f"Importando {z.name}", feito / total)
                    saida = novo / tabela / f"{n:02d}.parquet" if len(zips) > 1 or tabela in (
                        "estabelecimentos", "empresas", "socios") else novo / f"{tabela}.parquet"
                    saida.parent.mkdir(parents=True, exist_ok=True)
                    self._zip_para_parquet(con, z, tabela, saida, ufs, somente_ativas)
                    feito += 1
            progresso("Montando lista de cidades", 0.99)
            con.execute(f"""
                COPY (
                  SELECT e.uf, e.municipio AS codigo, m.descricao AS nome, count(*) AS empresas
                  FROM read_parquet('{novo}/estabelecimentos/*.parquet') e
                  LEFT JOIN read_parquet('{novo}/municipios.parquet') m ON m.codigo = e.municipio
                  WHERE e.uf IS NOT NULL AND e.uf <> 'EX'
                  GROUP BY ALL ORDER BY 1, 3
                ) TO '{novo}/cidades.parquet' (FORMAT parquet)
            """)
        finally:
            con.close()

        shutil.rmtree(self.dir_parquet, ignore_errors=True)
        novo.rename(self.dir_parquet)
        mes = (pasta_zips / ".completo").read_text().strip() if (pasta_zips / ".completo").exists() \
            else pasta_zips.name
        self.arq_meta.write_text(json.dumps({
            "mes": mes, "importado_em": datetime.now().isoformat(timespec="minutes"),
            "ufs": ufs or "todas", "somente_ativas": somente_ativas,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        if apagar_zips:
            shutil.rmtree(pasta_zips, ignore_errors=True)
        progresso("Base pronta!", 1.0)

    @staticmethod
    def _zip_para_parquet(con, arq_zip: Path, tabela: str, saida: Path,
                          ufs: list[str] | None, somente_ativas: bool) -> None:
        cols = COLUNAS[tabela]
        with tempfile.TemporaryDirectory(dir=saida.parent) as tmp, zipfile.ZipFile(arq_zip) as zf:
            membros = [m for m in zf.infolist() if not m.is_dir()]
            for m in membros:
                csv = Path(zf.extract(m, tmp))
                tipos = "{" + ", ".join(f"'{c}': 'VARCHAR'" for c in cols) + "}"
                origem = (f"read_csv('{csv}', delim=';', quote='\"', escape='\"', header=false, "
                          f"encoding='latin-1', columns={tipos}, null_padding=true, "
                          f"ignore_errors=true, parallel=true)")
                if tabela == "estabelecimentos":
                    sel = ", ".join(c for c in cols if c not in _ESTAB_DESCARTAR)
                    filtros = []
                    if somente_ativas:
                        filtros.append("situacao_cadastral = '02'")
                    if ufs:
                        filtros.append("uf IN (" + ", ".join(f"'{u}'" for u in ufs) + ")")
                    where = ("WHERE " + " AND ".join(filtros)) if filtros else ""
                    sql = f"SELECT {sel} FROM {origem} {where}"
                elif tabela == "socios":
                    sql = (f"SELECT cnpj_basico, identificador_socio, nome_socio, qualificacao_socio, "
                           f"data_entrada_sociedade, faixa_etaria FROM {origem}")
                elif tabela == "empresas":
                    sql = (f"SELECT cnpj_basico, razao_social, natureza_juridica, capital_social, porte "
                           f"FROM {origem}")
                else:
                    sql = f"SELECT * FROM {origem}"
                alvo = saida if len(membros) == 1 else saida.with_name(f"{saida.stem}_{m.filename[-8:]}.parquet")
                con.execute(f"COPY ({sql}) TO '{alvo}' (FORMAT parquet, COMPRESSION zstd)")
                csv.unlink(missing_ok=True)

    # ------------------------------------------------------------------ consultas
    def cidades(self) -> pd.DataFrame:
        con = self._con()
        try:
            return con.execute(f"SELECT * FROM read_parquet('{self._pq('cidades')}')").df()
        finally:
            con.close()

    def buscar_cnae(self, texto: str) -> pd.DataFrame:
        con = self._con()
        try:
            t = texto.strip()
            if re.fullmatch(r"[\d\-/. ]+", t):
                cod = re.sub(r"\D", "", t)
                return con.execute(
                    f"SELECT codigo, descricao FROM read_parquet('{self._pq('cnaes')}') "
                    f"WHERE codigo LIKE ? ORDER BY 1 LIMIT 50", [cod + "%"]).df()
            return con.execute(
                f"SELECT codigo, descricao FROM read_parquet('{self._pq('cnaes')}') "
                f"WHERE strip_accents(lower(descricao)) LIKE '%' || strip_accents(lower(?)) || '%' "
                f"ORDER BY 1 LIMIT 50", [t]).df()
        finally:
            con.close()

    def descricao_cnaes(self, codigos: list[str]) -> dict[str, str]:
        con = self._con()
        try:
            df = con.execute(f"SELECT codigo, descricao FROM read_parquet('{self._pq('cnaes')}') "
                             f"WHERE list_contains($c, codigo)", {"c": codigos}).df()
            return dict(zip(df.codigo, df.descricao))
        finally:
            con.close()

    def consultar(self, cnaes: list[str], municipio: str, incluir_secundario: bool = False,
                  somente_ativas: bool = True) -> pd.DataFrame:
        """Estabelecimentos do município com os CNAEs informados, já com razão social e sócios."""
        con = self._con()
        try:
            df = con.execute(f"""
                WITH e AS (
                  SELECT * FROM read_parquet('{self._pq('estabelecimentos')}')
                  WHERE municipio = $mun
                    AND (list_contains($cnaes, cnae_fiscal_principal)
                         OR ($sec AND list_has_any(string_split(coalesce(cnae_fiscal_secundaria, ''), ','), $cnaes)))
                    AND (NOT $ativas OR situacao_cadastral = '02')
                ),
                q AS (SELECT * FROM read_parquet('{self._pq('qualificacoes')}')),
                s AS (
                  SELECT s.cnpj_basico,
                         string_agg(s.nome_socio || coalesce(' (' || q.descricao || ')', ''), '; '
                                    ORDER BY (s.qualificacao_socio NOT IN ('49', '65', '05', '16', '10')),
                                             s.nome_socio) AS socios
                  FROM read_parquet('{self._pq('socios')}') s
                  JOIN (SELECT DISTINCT cnpj_basico FROM e) k USING (cnpj_basico)
                  LEFT JOIN q ON q.codigo = s.qualificacao_socio
                  GROUP BY 1
                )
                SELECT e.cnpj_basico || e.cnpj_ordem || e.cnpj_dv AS cnpj,
                       emp.razao_social, e.nome_fantasia, e.matriz_filial,
                       e.situacao_cadastral, e.data_inicio_atividade,
                       e.cnae_fiscal_principal, e.cnae_fiscal_secundaria,
                       e.tipo_logradouro, e.logradouro, e.numero, e.complemento,
                       e.bairro, e.cep, e.uf, e.municipio,
                       e.ddd_1, e.telefone_1, e.ddd_2, e.telefone_2,
                       lower(trim(e.correio_eletronico)) AS email,
                       emp.porte, emp.natureza_juridica, s.socios
                FROM e
                LEFT JOIN read_parquet('{self._pq('empresas')}') emp USING (cnpj_basico)
                LEFT JOIN s USING (cnpj_basico)
            """, {"mun": str(municipio), "cnaes": [str(c) for c in cnaes],
                  "sec": bool(incluir_secundario), "ativas": bool(somente_ativas)}).df()
        finally:
            con.close()
        df = df.fillna("")  # todas as colunas são texto
        df["bairro_norm"] = df["bairro"].map(norm_bairro)
        return df

    def listar_bairros(self, cnaes: list[str], municipio: str, incluir_secundario: bool = False) -> pd.DataFrame:
        df = self.consultar(cnaes, municipio, incluir_secundario)
        if df.empty:
            return pd.DataFrame(columns=["bairro", "empresas"])
        return (df.groupby("bairro_norm").size().reset_index(name="empresas")
                  .rename(columns={"bairro_norm": "bairro"})
                  .sort_values(["empresas", "bairro"], ascending=[False, True]))
