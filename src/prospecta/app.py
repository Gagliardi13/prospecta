"""Prospecta - tela web para encontrar empresas por segmento, cidade e bairro."""
from __future__ import annotations

import json
import os
import threading
import traceback
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st
import yaml

from prospecta.google_places import COTA_GRATIS_MES, PRECO_POR_MIL_USD, CacheBuscas, ContadorUso, Places
from prospecta.pipeline import Busca, _consultas_google, executar
from prospecta.receita import BASE_URL as URL_RECEITA, BaseReceita
from prospecta import __version__
from prospecta.util import norm_bairro, norm_texto

RAIZ = Path(__file__).parent
# Dados, configurações e resultados ficam fora do programa, para sobreviver às atualizações
DADOS = Path(os.environ.get("PROSPECTA_DADOS", Path.home() / "Prospecta"))
MEUS_SEGMENTOS = DADOS / "meus_segmentos.yaml"
_MODELO_MEUS = """# Seus segmentos personalizados (este arquivo nunca é sobrescrito pelas atualizações).
# Copie o modelo abaixo, tire o '#' do começo das linhas e ajuste. Reabra o programa para ver.
#
# Dentistas infantis:
#   cnaes: ["8630504"]
#   termos: ["odontopediatra"]
#   tipo_google: dentist
"""
RESULTADOS = DADOS / "resultados"
CONFIG = DADOS / "config.json"
UFS = ["AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA", "MG", "MS", "MT", "PA", "PB", "PE",
       "PI", "PR", "RJ", "RN", "RO", "RR", "RS", "SC", "SE", "SP", "TO"]
OUTRO = "➕ Outro segmento (escolher CNAE)"

st.set_page_config(page_title="Prospecta", page_icon="🔎", layout="wide")


# =========================================================================== infraestrutura
class Tarefa:
    """Roda uma função demorada em segundo plano (download da base, busca)."""

    def __init__(self, fn, *args):
        self.msg, self.frac = "Iniciando...", 0.0
        self.erro: str | None = None
        self.resultado = None
        self.fim = False
        self._t = threading.Thread(target=self._rodar, args=(fn, *args), daemon=True)
        self._t.start()

    def progresso(self, msg: str, frac: float | None = None) -> None:
        self.msg = msg
        if frac is not None:
            self.frac = max(0.0, min(1.0, float(frac)))

    def _rodar(self, fn, *args):
        try:
            self.resultado = fn(*args, self.progresso)
        except Exception as e:  # noqa: BLE001
            self.erro = f"{e}\n\n{traceback.format_exc(limit=3)}"
        finally:
            self.fim = True


@st.cache_resource
def tarefas() -> dict[str, Tarefa]:
    return {}


@st.cache_resource
def base(url_base: str) -> BaseReceita:
    return BaseReceita(DADOS / "receita", url_base)


def carregar_config() -> dict:
    padrao = {"google_api_key": "", "respeitar_cota": True, "limite_por_busca": 300,
              "url_receita": URL_RECEITA}
    try:
        return {**padrao, **json.loads(CONFIG.read_text(encoding="utf-8"))}
    except Exception:
        return padrao


def salvar_config(cfg: dict) -> None:
    DADOS.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


@st.cache_data
def segmentos() -> dict:
    segs = yaml.safe_load((RAIZ / "segmentos.yaml").read_text(encoding="utf-8")) or {}
    try:
        if not MEUS_SEGMENTOS.exists():
            MEUS_SEGMENTOS.parent.mkdir(parents=True, exist_ok=True)
            MEUS_SEGMENTOS.write_text(_MODELO_MEUS, encoding="utf-8")
        segs.update(yaml.safe_load(MEUS_SEGMENTOS.read_text(encoding="utf-8")) or {})
    except Exception as e:  # noqa: BLE001
        st.warning(f"Não consegui ler {MEUS_SEGMENTOS}: {e}")
    return segs


@st.cache_data(show_spinner=False)
def cidades(versao: str) -> pd.DataFrame:
    return br.cidades()


@st.cache_data(show_spinner="Contando empresas por bairro...")
def bairros(versao: str, cnaes: tuple, municipio: str, sec: bool) -> pd.DataFrame:
    return br.listar_bairros(list(cnaes), municipio, sec)


@st.cache_data(show_spinner=False)
def busca_cnae(versao: str, texto: str) -> pd.DataFrame:
    return br.buscar_cnae(texto)


def mostrar_tarefa(chave: str) -> Tarefa | None:
    t = tarefas().get(chave)
    if t and not t.fim:
        _acompanhar(chave)
    return t


@st.fragment(run_every=1.5)
def _acompanhar(chave: str):
    t = tarefas()[chave]
    st.progress(t.frac, text=t.msg)
    if t.fim:
        st.rerun()


def titulo_cidade(nome: str) -> str:
    minusc = {"De", "Da", "Do", "Das", "Dos", "E"}
    return " ".join(p if p not in minusc else p.lower() for p in str(nome).title().split())


cfg = carregar_config()
contador = ContadorUso(DADOS / "uso_google.json")
cache_google = CacheBuscas(DADOS / "cache_google")
br = base(cfg["url_receita"])
versao_base = json.dumps(br.info())

# =========================================================================== barra lateral
with st.sidebar:
    st.header("⚙️ Configurações")

    # --- Base da Receita
    st.subheader("Base de CNPJ (Receita)")
    t_base = tarefas().get("base")
    if t_base and not t_base.fim:
        st.info("Preparando a base... pode deixar rodando e voltar depois.")
        mostrar_tarefa("base")
    else:
        if t_base and t_base.erro:
            st.error("Falhou ao preparar a base:\n\n" + t_base.erro)
        if br.pronta():
            inf = br.info()
            ufs_txt = ", ".join(inf["ufs"]) if isinstance(inf.get("ufs"), list) else "Brasil todo"
            st.success(f"Base de **{inf.get('mes')}** pronta ({ufs_txt}).")
        else:
            st.warning("A base ainda não foi baixada. Sem ela você não tem os nomes dos sócios "
                       "nem a lista completa de CNPJs (dá para usar só o Google).")

        with st.expander("Baixar / atualizar base" if br.pronta() else "Baixar base agora", expanded=not br.pronta()):
            st.caption("A Receita atualiza a base todo mês. A primeira vez demora (download de alguns GB); "
                       "depois as buscas levam segundos.")
            ufs_sel = st.multiselect("Estados a guardar (vazio = Brasil todo)", UFS, default=["SP"],
                                     help="Guardar só os estados que você vai usar economiza muito espaço em disco.")
            apagar = st.checkbox("Apagar os .zip depois de importar (economiza espaço)", value=True)
            url_rec = st.text_input("Endereço dos arquivos na Receita", cfg["url_receita"],
                                    help="Só mude se a Receita trocar o endereço. Pode ser a pasta geral "
                                         "ou a pasta de um mês (termina em /AAAA-MM/).")
            if url_rec.strip() and url_rec.strip() != cfg["url_receita"]:
                cfg["url_receita"] = url_rec.strip()
                salvar_config(cfg)
                st.rerun()
            if st.button("⬇️ Baixar e preparar a base", width="stretch"):
                def _job(prog):
                    pasta = br.baixar(progresso=prog)
                    br.importar(pasta, ufs=ufs_sel or None, apagar_zips=apagar, progresso=prog)
                tarefas()["base"] = Tarefa(_job)
                st.rerun()
            st.divider()
            pasta_manual = DADOS / "receita" / "zips" / "manual"
            st.caption(f"Já baixou os .zip por fora? Coloque-os em `{pasta_manual}` e clique abaixo.")
            if st.button("📂 Importar da pasta", width="stretch"):
                pasta_manual.mkdir(parents=True, exist_ok=True)
                tarefas()["base"] = Tarefa(lambda prog: br.importar(
                    pasta_manual, ufs=ufs_sel or None, apagar_zips=False, progresso=prog))
                st.rerun()

    # --- Google
    st.subheader("Google Maps (opcional)")
    chave = st.text_input("Chave da API (Places API New)", value=cfg["google_api_key"], type="password",
                          help="Veja no LEIA-ME como criar a chave. Fica salva só no seu computador.")
    respeitar = st.checkbox(f"Nunca passar da cota grátis do mês ({COTA_GRATIS_MES} chamadas)",
                            value=cfg["respeitar_cota"])
    limite = st.number_input("Máximo de chamadas por busca", 10, 5000, int(cfg["limite_por_busca"]), step=10)
    usados = contador.usados_no_mes()
    st.caption(f"Usadas por este programa neste mês: **{usados}** de {COTA_GRATIS_MES} grátis.")
    novo_cfg = {**cfg, "google_api_key": chave.strip(), "respeitar_cota": respeitar,
                "limite_por_busca": int(limite)}
    if novo_cfg != cfg:
        salvar_config(novo_cfg)
        cfg = novo_cfg

# =========================================================================== tela principal
st.title("🔎 Prospecta")
st.sidebar.caption(f"Prospecta v{__version__} · seus dados ficam em `{DADOS}`")
st.caption("Encontre empresas de um segmento numa cidade ou bairro, com sócios, telefone, WhatsApp, e-mail e site.")

segs = segmentos()
c1, c2 = st.columns([1, 1], gap="large")

# --- 1. Segmento
with c1:
    st.subheader("1. Segmento")
    nome_seg = st.selectbox("Tipo de empresa", list(segs) + [OUTRO])
    if nome_seg == OUTRO:
        if not br.pronta():
            st.info("Para escolher qualquer CNAE, baixe a base da Receita (barra lateral). "
                    "Sem ela, informe só os termos de busca do Google.")
            cnaes, rotulo = [], st.text_input("Nome do segmento", "Personalizado")
        else:
            txt = st.text_input("Procurar atividade (nome ou código CNAE)", placeholder="ex.: odonto, 8630504")
            opcoes = {}
            if txt.strip():
                df_c = busca_cnae(versao_base, txt)
                opcoes = {f"{r.codigo} — {r.descricao}": r.codigo for r in df_c.itertuples()}
                if not opcoes:
                    st.caption("Nada encontrado.")
            escolhidos = st.multiselect("Atividades (CNAE)", list(opcoes))
            cnaes = [opcoes[e] for e in escolhidos]
            rotulo = escolhidos[0].split(" — ", 1)[1] if escolhidos else "Personalizado"
        termos_padrao = rotulo.lower() if rotulo != "Personalizado" else ""
        tipo_google = None
    else:
        s = segs[nome_seg]
        cnaes, rotulo = [str(c) for c in s.get("cnaes", [])], nome_seg
        termos_padrao = ", ".join(s.get("termos", []))
        tipo_google = s.get("tipo_google")
        st.caption("CNAE: " + ", ".join(cnaes))
    termos_txt = st.text_input("Termos de busca no Google (separados por vírgula)", termos_padrao,
                               key=f"termos_{nome_seg}")
    termos = [t.strip() for t in termos_txt.split(",") if t.strip()]
    incluir_sec = st.checkbox("Incluir empresas com essa atividade como secundária",
                              help="Pega mais empresas, mas algumas podem não ser do ramo de fato.")

# --- 2. Local
with c2:
    st.subheader("2. Local")
    municipio_codigo, bairros_sel, bairros_cidade = None, [], []
    if br.pronta():
        df_cid = cidades(versao_base)
        ufs_disp = sorted(df_cid["uf"].unique())
        uf = st.selectbox("Estado", ufs_disp, index=ufs_disp.index("SP") if "SP" in ufs_disp else 0)
        df_uf = df_cid[df_cid["uf"] == uf].sort_values("nome")
        nomes = df_uf["nome"].tolist()
        padrao = nomes.index("SAO BERNARDO DO CAMPO") if "SAO BERNARDO DO CAMPO" in nomes else 0
        nome_cid = st.selectbox("Cidade", nomes, index=padrao, format_func=titulo_cidade)
        municipio_codigo = str(df_uf.iloc[nomes.index(nome_cid)]["codigo"])
        cidade = titulo_cidade(nome_cid)
        if cnaes:
            df_b = bairros(versao_base, tuple(cnaes), municipio_codigo, incluir_sec)
            bairros_cidade = [b for b in df_b["bairro"] if b != "(SEM BAIRRO)"]
            rot_b = {r.bairro: f"{titulo_cidade(r.bairro)} ({r.empresas})" for r in df_b.itertuples()}
            bairros_sel = st.multiselect("Bairros (vazio = cidade inteira)", list(rot_b),
                                         format_func=lambda b: rot_b[b],
                                         help="O número entre parênteses é quantas empresas a Receita tem ali.")
            st.caption(f"A Receita tem **{int(df_b['empresas'].sum())}** empresas desse segmento em {cidade}.")
    else:
        uf = st.selectbox("Estado", UFS, index=UFS.index("SP"))
        cidade = st.text_input("Cidade", "São Bernardo do Campo")
        b_txt = st.text_input("Bairros (opcional, separados por vírgula)")
        bairros_sel = [norm_bairro(b) for b in b_txt.split(",") if b.strip()]

# --- 3. Fontes
st.subheader("3. O que buscar")
tem_chave = bool(cfg["google_api_key"])
f1, f2, f3, f4 = st.columns(4)
usar_google = f1.checkbox("Google Maps (telefone, site, nota)", value=tem_chave, disabled=not tem_chave,
                          help=None if tem_chave else "Coloque a chave do Google na barra lateral.")
n_b = len(bairros_cidade)
opcoes_varr = {"Não (só a cidade)": 0, "15 bairros com mais empresas": 15}
if n_b > 15:
    opcoes_varr[f"Todos os {n_b} bairros (cobertura máxima)"] = None
escolha_varr = f2.selectbox("Busca bairro a bairro", list(opcoes_varr), index=1,
                            disabled=bool(bairros_sel) or not usar_google,
                            help="O Google devolve no máximo 60 resultados por busca. Buscar bairro a bairro acha "
                                 "muito mais. Bairros e empresas repetidos são descartados, e buscas já feitas nos "
                                 "últimos 30 dias são reaproveitadas sem gastar cota.")
max_b = opcoes_varr[escolha_varr]
varrer = max_b != 0
individual = f3.checkbox("Procurar no Google cada CNPJ não encontrado", value=False, disabled=not usar_google,
                         help="Gasta 1 chamada por empresa. Melhora telefone/site, mas consome a cota rápido.")
sites = f4.checkbox("Ler os sites (e-mail e WhatsApp)", value=True)

busca = Busca(segmento=rotulo, cnaes=cnaes, termos=termos, uf=uf, cidade=cidade,
              municipio_codigo=municipio_codigo, bairros=bairros_sel, tipo_google=tipo_google,
              incluir_secundario=incluir_sec, usar_google=usar_google, varrer_bairros=varrer,
              max_bairros_varredura=max_b,
              buscar_individual=individual, visitar_sites=sites)

limite_busca = int(cfg["limite_por_busca"])
if cfg["respeitar_cota"]:
    limite_busca = min(limite_busca, max(0, COTA_GRATIS_MES - contador.usados_no_mes()))
if usar_google:
    consultas = _consultas_google(busca, bairros_cidade)
    novas = [q for q in consultas if cache_google.ler(q, tipo_google) is None]
    n_cons = len(novas)
    estimativa = n_cons * 3
    msg = (f"{len(consultas)} buscas no Google ({len(consultas) - n_cons} já feitas nos últimos 30 dias, sem custo). "
           f"Até **{estimativa}** chamadas nas novas (até 3 páginas cada, normalmente bem menos)"
           + (" + 1 por CNPJ não encontrado" if individual else "")
           + f". Limite desta busca: **{limite_busca}** chamadas")
    if not cfg["respeitar_cota"]:
        msg += f" (acima da cota grátis, ~US$ {PRECO_POR_MIL_USD / 1000:.3f} por chamada)"
    st.caption(msg + ".")
    if limite_busca == 0:
        st.warning("A cota grátis deste mês acabou. A busca vai usar só a Receita "
                   "(ou desmarque 'Nunca passar da cota grátis' na barra lateral).")

pode = (bool(cnaes) and br.pronta()) or (usar_google and bool(termos))
t_busca = tarefas().get("busca")
rodando = bool(t_busca and not t_busca.fim)
if st.button("🔎 Buscar", type="primary", disabled=not pode or rodando, width="stretch"):
    cache_google.limpar_vencidos()
    places = (Places(cfg["google_api_key"], limite_busca, contador, cache=cache_google)
              if usar_google and limite_busca else None)
    if places is None:
        busca.usar_google = False
    tarefas()["busca"] = Tarefa(executar, busca, br if br.pronta() else None, places)
    tarefas()["busca"].busca = busca
    st.rerun()
if not pode:
    st.caption("Para buscar: escolha um segmento com CNAE (com a base baixada) ou ative o Google com pelo menos um termo.")

# =========================================================================== resultado
t_busca = mostrar_tarefa("busca")
if t_busca and t_busca.fim:
    if t_busca.erro:
        st.error("A busca falhou:\n\n" + t_busca.erro)
    elif t_busca.resultado is not None:
        df, stats = t_busca.resultado
        if stats.get("aviso"):
            st.warning(stats["aviso"])
        st.divider()
        st.subheader(f"Resultado: {len(df)} empresas")
        if df.empty:
            st.info("Nada encontrado com esses filtros.")
        else:
            m = st.columns(6)
            m[0].metric("Empresas", len(df))
            m[1].metric("Com sócio/responsável", int((df["socios_responsaveis"] != "").sum()))
            m[2].metric("Com telefone", int((df["telefone_principal"] != "").sum()))
            m[3].metric("Com WhatsApp", int((df["whatsapp"] != "").sum()),
                        help=f"{int((df['whatsapp_status'] == 'confirmado no site').sum())} confirmados no site")
            m[4].metric("Com e-mail", int((df["emails"] != "").sum()))
            m[5].metric("Com site", int((df["site"] != "").sum()))
            st.caption(f"Receita: {stats['receita']} · Google: {stats['google_lugares']} lugares "
                       f"({stats['casados']} cruzados com CNPJ, {stats['so_google']} só no Google) · "
                       f"Chamadas ao Google: {stats['chamadas_google']} "
                       f"(+ {stats.get('buscas_do_cache', 0)} buscas reaproveitadas sem custo)")
            st.dataframe(df, width="stretch", hide_index=True,
                         column_config={"site": st.column_config.LinkColumn(),
                                        "link_maps": st.column_config.LinkColumn(),
                                        "completude": st.column_config.ProgressColumn(min_value=0, max_value=5,
                                                                                      format="%d/5")})
            RESULTADOS.mkdir(parents=True, exist_ok=True)
            nome_arq = norm_texto(f"{df['segmento'].iloc[0]} {df['cidade'].iloc[0]}").lower().replace(" ", "_")
            b_usada = getattr(t_busca, "busca", busca)
            if b_usada.bairros:
                nome_arq += "_" + "_".join(norm_texto(b).lower().replace(" ", "-") for b in b_usada.bairros[:3])
            nome_arq += datetime.now().strftime("_%Y%m%d_%H%M")
            arq = RESULTADOS / f"{nome_arq}.csv"
            if not getattr(t_busca, "salvo", None):
                df.to_csv(arq, index=False, sep=";", encoding="utf-8-sig")
                t_busca.salvo = arq
            d1, d2 = st.columns(2)
            d1.download_button("⬇️ CSV para Excel (;)", df.to_csv(index=False, sep=";").encode("utf-8-sig"),
                               file_name=f"{nome_arq}.csv", mime="text/csv", width="stretch")
            d2.download_button("⬇️ CSV padrão (,) para automações", df.to_csv(index=False).encode("utf-8"),
                               file_name=f"{nome_arq}_padrao.csv", mime="text/csv", width="stretch")
            st.caption(f"Uma cópia foi salva em `{t_busca.salvo}`.")

# --- histórico
anteriores = sorted(RESULTADOS.glob("*.csv"), reverse=True) if RESULTADOS.exists() else []
if anteriores:
    with st.expander(f"📁 Buscas anteriores ({len(anteriores)})"):
        for a in anteriores[:30]:
            st.download_button(a.name, a.read_bytes(), file_name=a.name, mime="text/csv", key=f"h_{a.name}")
