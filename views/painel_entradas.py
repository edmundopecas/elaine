"""
Painel de Entradas — visão executiva do faturamento.

Espelha o Painel de Saídas (apresentacao.py), mas pra receita: separa o
FATURAMENTO real (entra_dre=1: venda, aluguel, rendimento) do MOVIMENTO INTERNO
(transferência entre contas / aplicação-resgate, entra_dre=0, que NÃO é dinheiro
que entrou de verdade). Mostra também **por forma de recebimento** (PIX, cartão,
boleto, TED) — pedido do Filipe.
"""
from __future__ import annotations

from datetime import date

import altair as alt
import pandas as pd
import streamlit as st

from db import query


def brl(v: float) -> str:
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


from tema import (POSITIVO, FLORESTA, TERRACOTA, OCRE, FLORESTA_CLARO, CINZA,
                  TEXTO)

# Paleta por forma de recebimento (família Terracota · Floresta · Creme)
COR_FORMA = {"Cartão": FLORESTA, "PIX": TERRACOTA, "Boleto": OCRE,
             "TED/Transferência": FLORESTA_CLARO, "Outros": CINZA}
ORDEM_FORMA = ["Cartão", "PIX", "Boleto", "TED/Transferência", "Outros"]


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
    if "TED" in s or "DOC" in s or "TRANSFER" in s:
        return "TED/Transferência"
    return "Outros"


st.title("💰 Painel de Entradas")

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

cond, params = ["l.tipo='entrada'", "l.data BETWEEN ? AND ?"], [d_ini.isoformat(), d_fim.isoformat()]
if sel_emp != "Todas":
    eid = next(e["id"] for e in empresas if e["apelido"] == sel_emp)
    cond.append("l.empresa_id=?"); params.append(eid)
where = " AND ".join(cond)

# ── Linhas de entrada (com categoria e DRE) ──────────────────────────────────
linhas = query(
    f"""SELECT l.valor, l.contraparte, l.descricao, COALESCE(p.nome,'A classificar') AS cat,
        p.entra_dre AS dre, e.apelido AS emp
        FROM lancamentos l LEFT JOIN plano_contas p ON p.id=l.plano_conta_id
        JOIN empresas e ON e.id=l.empresa_id WHERE {where}""",
    tuple(params),
)
if not linhas:
    st.info("Sem entradas no período selecionado.")
    st.stop()

receita = [r for r in linhas if r["dre"] == 1]
interno = [r for r in linhas if r["dre"] == 0]
pend = [r for r in linhas if r["dre"] is None]
tot_fat = sum(r["valor"] for r in receita)
tot_int = sum(r["valor"] for r in interno)
tot_pend = sum(r["valor"] for r in pend)
n_pend = len(pend)
vendas = sum(r["valor"] for r in receita if "Venda" in r["cat"])
aluguel = sum(r["valor"] for r in receita if "Aluguel" in r["cat"])

titulo = "Grupo Edmundo" if sel_emp == "Todas" else sel_emp
st.caption(f"**{titulo}** · {d_ini.strftime('%d/%m/%Y')} a {d_fim.strftime('%d/%m/%Y')}")

# ── KPIs ─────────────────────────────────────────────────────────────────────
k = st.columns(4)
k[0].metric("💰 Recebimento", brl(tot_fat),
            help="Receita de verdade (venda + aluguel + rendimento). Entra no resultado.")
k[1].metric("🛒 Vendas", brl(vendas),
            f"{(vendas / tot_fat * 100):.0f}% do faturamento" if tot_fat else None,
            delta_color="off")
k[2].metric("🏠 Aluguel", brl(aluguel),
            f"{(aluguel / tot_fat * 100):.0f}% do faturamento" if tot_fat else None,
            delta_color="off")
k[3].metric("🔄 Movimento interno", brl(tot_int),
            help="Transferência entre contas e resgate de aplicação — NÃO é faturamento.")

st.divider()

# ── Por forma de recebimento (donut) + por empresa (barras) ───────────────────
col_a, col_b = st.columns([1, 1.4])

with col_a:
    st.subheader("Como o dinheiro entrou")
    formas = {}
    for r in receita:
        f = forma_recebimento(f"{r['contraparte'] or ''} {r['descricao'] or ''}")
        formas[f] = formas.get(f, 0) + r["valor"]
    fdf = pd.DataFrame([{"Forma": f, "Valor": v} for f, v in formas.items() if v > 0])
    if not fdf.empty:
        donut = (
            alt.Chart(fdf)
            .mark_arc(innerRadius=70, stroke="#F6F6EF", strokeWidth=2)
            .encode(
                theta=alt.Theta("Valor:Q", stack=True),
                color=alt.Color("Forma:N", legend=alt.Legend(orient="bottom", title=None),
                                scale=alt.Scale(domain=ORDEM_FORMA,
                                                range=[COR_FORMA[f] for f in ORDEM_FORMA])),
                tooltip=["Forma:N", alt.Tooltip("Valor:Q", format=",.2f", title="R$")],
            )
            .properties(height=300)
        )
        st.altair_chart(donut, use_container_width=True)
        maior = max(formas.items(), key=lambda x: x[1])
        st.caption(f"Forma que mais trouxe dinheiro: **{maior[0]}** ({brl(maior[1])}).")

with col_b:
    st.subheader("Faturamento por empresa")
    por_emp = {}
    for r in receita:
        por_emp[r["emp"]] = por_emp.get(r["emp"], 0) + r["valor"]
    if por_emp:
        edf = pd.DataFrame([{"Empresa": e, "Valor": v, "Label": brl(v)}
                            for e, v in sorted(por_emp.items(), key=lambda x: -x[1])])
        be = alt.Chart(edf).encode(
            x=alt.X("Valor:Q", title=None, axis=alt.Axis(format="~s"),
                    scale=alt.Scale(domain=[0, max(por_emp.values()) * 1.22])),
            y=alt.Y("Empresa:N", sort="-x", title=None))
        st.altair_chart(
            (be.mark_bar(color=POSITIVO, cornerRadiusEnd=4).encode(
                tooltip=["Empresa:N", alt.Tooltip("Valor:Q", format=",.2f", title="R$")])
             + be.mark_text(align="left", dx=4, color=TEXTO, fontSize=12).encode(text="Label:N")
             ).properties(height=60 + 32 * len(edf)),
            use_container_width=True)

# ── Tabela: forma de recebimento detalhada ────────────────────────────────────
st.divider()
st.subheader("📊 Total por forma de recebimento")
formas_n = {}
for r in receita:
    f = forma_recebimento(f"{r['contraparte'] or ''} {r['descricao'] or ''}")
    d = formas_n.setdefault(f, [0, 0.0])
    d[0] += 1; d[1] += r["valor"]
tabela = pd.DataFrame(
    [{"Forma": f, "Qtd": formas_n[f][0], "Total": formas_n[f][1],
      "% do faturamento": (formas_n[f][1] / tot_fat * 100) if tot_fat else 0}
     for f in ORDEM_FORMA if f in formas_n])
st.dataframe(
    tabela, hide_index=True, use_container_width=True,
    column_config={
        "Total": st.column_config.NumberColumn(format="R$ %.2f"),
        "% do faturamento": st.column_config.NumberColumn(format="%.1f%%"),
    })

# ── Rodapé ────────────────────────────────────────────────────────────────────
st.divider()
if tot_pend > 0:
    st.info(f"ℹ️ Ainda há **{brl(tot_pend)}** em {n_pend} entradas a classificar.")
st.caption("Movimento interno (transferência entre contas e resgate de aplicação) é "
           "excluído do faturamento — não é dinheiro novo, só circulou.")
