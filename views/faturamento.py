"""
Análise do Faturamento — auditoria detalhada da receita.

Diferente do *Painel de Entradas* (visão executiva), esta tela serve pra
ABRIR TUDO e conferir lançamento a lançamento o que está somando no
faturamento (entra_dre=1). Foco em achar o que está estranho:

  • Pontos de atenção: possível transferência interna contada como receita,
    possíveis duplicatas (mesma empresa+valor+data) e os maiores lançamentos.
  • Faturamento por dia (picos).
  • Tabela completa, buscável/ordenável/exportável de cada entrada do faturamento.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date

import altair as alt
import pandas as pd
import streamlit as st

from db import query
from tema import POSITIVO, NAVY, TERRACOTA, TEXTO


def brl(v: float) -> str:
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _norm(s) -> str:
    if not isinstance(s, str):   # robusto a None / NaN (pandas)
        return ""
    s = unicodedata.normalize("NFKD", s).encode("ASCII", "ignore").decode()
    return re.sub(r"\s+", " ", s).strip().lower()


def forma_recebimento(texto: str) -> str:
    """Classifica a forma de recebimento pela descrição/contraparte do extrato."""
    s = (texto or "").upper()
    if any(t in s for t in ("CIELO", "LIBERA", "VINCULAD", "ANTECIP",
                            "DISPONIVEL CREDITO", "DISPONIVEL DEBITO",
                            "CREDITO AMEX", "CREDITO ELO", "CREDITO VISA",
                            "CREDITO MASTER", "CREDITO HIPER", "DEBITO ELO",
                            "DEBITO VISA", "DEBITO MASTER")):
        return "Cartão"
    if "BOLETO" in s or "COBRAN" in s or "COB BLOQ" in s:
        return "Boleto"
    if "PIX" in s:
        return "PIX"
    if "DEP" in s and "DINHEIRO" in s:
        return "Dinheiro"
    if "TED" in s or "DOC" in s or "TRANSFER" in s:
        return "TED/Transferência"
    return "Outros"


st.title("🔎 Análise do Faturamento")
st.caption("Confira tudo que está somando no faturamento (receita que entra no "
           "resultado). Use os **Pontos de atenção** pra achar o que está estranho.")

# ── Filtros ──────────────────────────────────────────────────────────────────
empresas = query("SELECT id, apelido FROM empresas WHERE ativa=1 ORDER BY apelido")
ext = query("SELECT MIN(data) lo, MAX(data) hi FROM lancamentos WHERE tipo='entrada'")[0]
lo = pd.to_datetime(ext["lo"]).date() if ext["lo"] else date.today()
hi = pd.to_datetime(ext["hi"]).date() if ext["hi"] else date.today()

c1, c2 = st.columns([1, 2])
sel_emp = c1.selectbox("Empresa", ["Todas"] + [e["apelido"] for e in empresas])
periodo = c2.date_input("Período", value=(lo, hi), min_value=lo, max_value=hi,
                        format="DD/MM/YYYY")
if isinstance(periodo, (list, tuple)):
    d_ini, d_fim = (periodo[0], periodo[-1]) if periodo else (lo, hi)
else:
    d_ini = d_fim = periodo

cond = ["l.tipo='entrada'", "p.entra_dre=1", "l.data BETWEEN ? AND ?"]
params = [d_ini.isoformat(), d_fim.isoformat()]
if sel_emp != "Todas":
    eid = next(e["id"] for e in empresas if e["apelido"] == sel_emp)
    cond.append("l.empresa_id=?"); params.append(eid)
where = " AND ".join(cond)

# ── Lançamentos que compõem o faturamento ─────────────────────────────────────
linhas = query(
    f"""SELECT l.id, l.data, e.apelido AS empresa,
        COALESCE(cb.descricao, cb.banco, '—') AS conta,
        COALESCE(l.contraparte, l.descricao, '(sem descrição)') AS quem,
        l.descricao, l.contraparte, l.cnpj_contraparte,
        p.nome AS categoria, l.valor
        FROM lancamentos l
        JOIN plano_contas p ON p.id=l.plano_conta_id
        LEFT JOIN empresas e ON e.id=l.empresa_id
        LEFT JOIN contas_bancarias cb ON cb.id=l.conta_bancaria_id
        WHERE {where} ORDER BY l.valor DESC""",
    tuple(params),
)
if not linhas:
    st.info("Sem faturamento no período/empresa selecionado.")
    st.stop()

df = pd.DataFrame(linhas)
df["forma"] = df.apply(
    lambda r: forma_recebimento(f"{r['contraparte'] or ''} {r['descricao'] or ''}"), axis=1)

tot = df["valor"].sum()
vendas = df.loc[df["categoria"].str.contains("Venda", na=False), "valor"].sum()
aluguel = df.loc[df["categoria"].str.contains("Aluguel", na=False), "valor"].sum()
rend = df.loc[df["categoria"].str.contains("Rendiment", na=False), "valor"].sum()

titulo = "Grupo Edmundo" if sel_emp == "Todas" else sel_emp
st.caption(f"**{titulo}** · {d_ini.strftime('%d/%m/%Y')} a {d_fim.strftime('%d/%m/%Y')} "
           f"· {len(df)} lançamentos")

# ── KPIs ─────────────────────────────────────────────────────────────────────
k = st.columns(4)
k[0].metric("💰 Faturamento", brl(tot), f"{len(df)} lançamentos", delta_color="off")
k[1].metric("🛒 Vendas", brl(vendas),
            f"{vendas / tot * 100:.0f}%" if tot else None, delta_color="off")
k[2].metric("🏠 Aluguel", brl(aluguel),
            f"{aluguel / tot * 100:.0f}%" if tot else None, delta_color="off")
k[3].metric("📈 Rendimento", brl(rend),
            f"{rend / tot * 100:.1f}%" if tot else None, delta_color="off")

# ── 🔍 Pontos de atenção ──────────────────────────────────────────────────────
st.divider()
st.subheader("🔍 Pontos de atenção")
st.caption("O que pode estar inflando o faturamento sem ser venda de verdade. "
           "Reveja cada um — se for transferência interna, reclassifique na tela de Entradas.")

# Tokens de empresas do grupo (apelido) + nomes internos conhecidos.
tokens_grupo = set()
for e in empresas:
    for w in _norm(e["apelido"]).split():
        if len(w) >= 4 and w not in ("matriz", "filial", "participacoes"):
            tokens_grupo.add(w)
tokens_grupo |= {"edmundo", "braga", "supernova", "rosilene", "ambrozio",
                 "ambrosio", "ferro velho", "eb particip"}


def _suspeita_interna(row) -> str | None:
    # nome do grupo em qualquer lugar (contraparte ou descrição) = forte sinal
    texto = _norm(f"{row['quem']} {row['descricao'] or ''}")
    achados = [t for t in tokens_grupo if t in texto]
    if achados:
        return "cita: " + ", ".join(sorted(set(achados))[:3])
    # 'transf' na CONTRAPARTE (não na descrição): o pagador é o próprio texto
    # genérico "PIX RECEBIDO TRANSF" — transferência anônima, típica de dinheiro
    # que circulou entre contas do grupo. Quando a contraparte é um nome de
    # cliente, é só alguém pagando por transferência — não é suspeito.
    if "transf" in _norm(row["contraparte"]):
        return "transferência sem pagador identificado"
    return None


df["_flag"] = df.apply(_suspeita_interna, axis=1)
suspeitas = df[df["_flag"].notna()].copy()

t1, t2, t3 = st.tabs([
    f"🔁 Possível movimento interno ({len(suspeitas)})",
    "👯 Possíveis duplicatas",
    "🔝 Maiores lançamentos",
])

with t1:
    if suspeitas.empty:
        st.success("Nenhum lançamento do faturamento parece ser transferência interna. 👍")
    else:
        st.caption(f"**{brl(suspeitas['valor'].sum())}** em {len(suspeitas)} lançamentos "
                   "que mencionam empresa do grupo ou 'transferência' — confira se "
                   "alguma é dinheiro que só circulou (não é venda).")
        vis = suspeitas[["data", "empresa", "conta", "quem", "categoria", "valor", "_flag"]] \
            .rename(columns={"data": "Data", "empresa": "Empresa", "conta": "Conta",
                             "quem": "Quem", "categoria": "Categoria", "valor": "Valor",
                             "_flag": "Por quê"})
        st.dataframe(vis, hide_index=True, use_container_width=True,
                     column_config={"Valor": st.column_config.NumberColumn(format="R$ %.2f")})

with t2:
    dups = df[df.duplicated(subset=["empresa", "data", "valor"], keep=False)] \
        .sort_values(["valor", "empresa", "data"], ascending=[False, True, True])
    if dups.empty:
        st.success("Nenhuma duplicata (mesma empresa, valor e data). 👍")
    else:
        st.caption(f"**{len(dups)} lançamentos** com mesma empresa + valor + data — "
                   "podem ser cobrança dupla, reimportação ou simplesmente parcelas "
                   "iguais legítimas. Confira.")
        vis = dups[["data", "empresa", "conta", "quem", "categoria", "valor"]] \
            .rename(columns={"data": "Data", "empresa": "Empresa", "conta": "Conta",
                             "quem": "Quem", "categoria": "Categoria", "valor": "Valor"})
        st.dataframe(vis, hide_index=True, use_container_width=True,
                     column_config={"Valor": st.column_config.NumberColumn(format="R$ %.2f")})

with t3:
    top = df.nlargest(15, "valor")[["data", "empresa", "conta", "quem", "forma",
                                    "categoria", "valor"]] \
        .rename(columns={"data": "Data", "empresa": "Empresa", "conta": "Conta",
                         "quem": "Quem", "forma": "Forma", "categoria": "Categoria",
                         "valor": "Valor"})
    st.caption("As 15 maiores entradas — comece por aqui, é onde mora o dinheiro grande.")
    st.dataframe(top, hide_index=True, use_container_width=True,
                 column_config={"Valor": st.column_config.NumberColumn(format="R$ %.2f")})

# ── Faturamento por dia ───────────────────────────────────────────────────────
st.divider()
st.subheader("📅 Faturamento por dia")
por_dia = df.groupby("data", as_index=False)["valor"].sum()
por_dia["Dia"] = pd.to_datetime(por_dia["data"]).dt.strftime("%d/%m")
bar = alt.Chart(por_dia).mark_bar(color=POSITIVO, cornerRadiusEnd=3).encode(
    x=alt.X("Dia:N", sort=list(por_dia.sort_values("data")["Dia"]), title=None),
    y=alt.Y("valor:Q", title=None, axis=alt.Axis(format="~s")),
    tooltip=["Dia:N", alt.Tooltip("valor:Q", format=",.2f", title="R$")],
).properties(height=260)
st.altair_chart(bar, use_container_width=True)
maior_dia = por_dia.loc[por_dia["valor"].idxmax()]
st.caption(f"Dia de maior faturamento: **{maior_dia['Dia']}** ({brl(maior_dia['valor'])}).")

# ── Tabela completa ───────────────────────────────────────────────────────────
st.divider()
st.subheader("📋 Todos os lançamentos do faturamento")

cf1, cf2 = st.columns([2, 1])
busca = cf1.text_input("🔎 Buscar (cliente / descrição / categoria)",
                       placeholder="ex: cielo, pix, aluguel, edmundo…")
forma_sel = cf2.selectbox("Forma de recebimento",
                          ["Todas"] + sorted(df["forma"].unique().tolist()))

vis = df.copy()
if busca.strip():
    b = _norm(busca)
    mask = (vis["quem"].map(_norm).str.contains(b, regex=False)
            | vis["descricao"].map(_norm).str.contains(b, regex=False)
            | vis["categoria"].map(_norm).str.contains(b, regex=False))
    vis = vis[mask]
if forma_sel != "Todas":
    vis = vis[vis["forma"] == forma_sel]

st.caption(f"Mostrando **{len(vis)}** lançamentos · soma **{brl(vis['valor'].sum())}**")
tabela = vis[["data", "empresa", "conta", "quem", "forma", "categoria", "valor"]] \
    .rename(columns={"data": "Data", "empresa": "Empresa", "conta": "Conta",
                     "quem": "Quem", "forma": "Forma", "categoria": "Categoria",
                     "valor": "Valor"})
st.dataframe(tabela, hide_index=True, use_container_width=True, height=460,
             column_config={"Valor": st.column_config.NumberColumn(format="R$ %.2f")})

st.download_button(
    "⬇️ Baixar CSV", tabela.to_csv(index=False).encode("utf-8-sig"),
    file_name=f"faturamento_{d_ini.isoformat()}_a_{d_fim.isoformat()}.csv",
    mime="text/csv")
