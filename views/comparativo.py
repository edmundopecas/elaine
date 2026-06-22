"""
Comparativo — Entrou × Saiu × Resultado, por empresa.

Confronta o FATURAMENTO (entradas que entram na DRE) com a DESPESA REAL (saídas
que entram na DRE) de cada empresa, e mostra o resultado (sobra/déficit). Tudo
que é movimento interno (entra_dre=0) fica de fora dos dois lados — senão a
transferência entre as contas do grupo distorceria a leitura.
"""
from __future__ import annotations

from datetime import date

import altair as alt
import pandas as pd
import streamlit as st

from db import query


def brl(v: float) -> str:
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


from tema import POSITIVO as VERDE, NEGATIVO as VERMELHO

st.title("⚖️ Comparativo — Entrou × Saiu")

# ── Filtro de período (sempre o grupo todo; o foco é comparar empresas) ───────
ext = query("SELECT MIN(data) lo, MAX(data) hi FROM lancamentos")[0]
lo = pd.to_datetime(ext["lo"]).date() if ext["lo"] else date.today()
hi = pd.to_datetime(ext["hi"]).date() if ext["hi"] else date.today()
periodo = st.date_input("Período", value=(lo, hi), min_value=lo, max_value=hi,
                        format="DD/MM/YYYY")
if isinstance(periodo, (list, tuple)):
    d_ini, d_fim = (periodo[0], periodo[-1]) if periodo else (lo, hi)
else:
    d_ini = d_fim = periodo
par = (d_ini.isoformat(), d_fim.isoformat())

# ── Entrou (faturamento) e Saiu (despesa real) por empresa ────────────────────
entrou = {r["emp"]: r["v"] for r in query(
    """SELECT e.apelido emp, SUM(l.valor) v FROM lancamentos l
       JOIN plano_contas p ON p.id=l.plano_conta_id JOIN empresas e ON e.id=l.empresa_id
       WHERE l.tipo='entrada' AND p.entra_dre=1 AND l.data BETWEEN ? AND ?
       GROUP BY e.apelido""", par)}
saiu = {r["emp"]: r["v"] for r in query(
    """SELECT e.apelido emp, SUM(l.valor) v FROM lancamentos l
       JOIN plano_contas p ON p.id=l.plano_conta_id JOIN empresas e ON e.id=l.empresa_id
       WHERE l.tipo='saida' AND p.entra_dre=1 AND l.data BETWEEN ? AND ?
       GROUP BY e.apelido""", par)}

empresas = sorted(set(entrou) | set(saiu),
                  key=lambda e: -(entrou.get(e, 0) + saiu.get(e, 0)))
if not empresas:
    st.info("Sem dados no período selecionado.")
    st.stop()

dados = [{"Empresa": e, "Entrou": entrou.get(e, 0.0), "Saiu": saiu.get(e, 0.0),
          "Resultado": entrou.get(e, 0.0) - saiu.get(e, 0.0)} for e in empresas]

tot_e = sum(d["Entrou"] for d in dados)
tot_s = sum(d["Saiu"] for d in dados)
tot_r = tot_e - tot_s

st.caption(f"**Grupo Edmundo** · {d_ini.strftime('%d/%m/%Y')} a {d_fim.strftime('%d/%m/%Y')}")

# ── KPIs do grupo ─────────────────────────────────────────────────────────────
k = st.columns(3)
k[0].metric("💰 Entrou (recebimento)", brl(tot_e))
k[1].metric("💸 Saiu (despesa real)", brl(tot_s))
k[2].metric("📈 Resultado", brl(tot_r),
            "sobrou" if tot_r >= 0 else "faltou",
            delta_color="normal" if tot_r >= 0 else "inverse")

st.caption("Só entram aqui os valores que afetam o resultado (DRE). Transferências "
           "entre as contas do grupo e aplicações ficam de fora dos dois lados.")
st.divider()

# ── Barras agrupadas Entrou × Saiu por empresa ────────────────────────────────
st.subheader("Entrou × Saiu por empresa")
long = pd.DataFrame(
    [{"Empresa": d["Empresa"], "Tipo": "Entrou", "Valor": d["Entrou"]} for d in dados] +
    [{"Empresa": d["Empresa"], "Tipo": "Saiu", "Valor": d["Saiu"]} for d in dados])
ch = (
    alt.Chart(long)
    .mark_bar(cornerRadiusEnd=3)
    .encode(
        x=alt.X("Valor:Q", title=None, axis=alt.Axis(format="~s")),
        y=alt.Y("Empresa:N", sort=empresas, title=None),
        yOffset="Tipo:N",
        color=alt.Color("Tipo:N", scale=alt.Scale(domain=["Entrou", "Saiu"],
                                                   range=[VERDE, VERMELHO]),
                        legend=alt.Legend(orient="top", title=None)),
        tooltip=["Empresa:N", "Tipo:N", alt.Tooltip("Valor:Q", format=",.2f", title="R$")],
    )
    .properties(height=80 + 55 * len(empresas))
)
st.altair_chart(ch, use_container_width=True)

# ── Resultado por empresa (verde sobra / vermelho déficit) ────────────────────
st.subheader("Resultado por empresa (sobrou ou faltou)")
rdf = pd.DataFrame([{"Empresa": d["Empresa"], "Resultado": d["Resultado"],
                     "Label": brl(d["Resultado"])} for d in dados])
base = alt.Chart(rdf).encode(
    x=alt.X("Resultado:Q", title=None, axis=alt.Axis(format="~s")),
    y=alt.Y("Empresa:N", sort=empresas, title=None))
barras = base.mark_bar(cornerRadiusEnd=3).encode(
    color=alt.condition(alt.datum.Resultado >= 0, alt.value(VERDE), alt.value(VERMELHO)),
    tooltip=["Empresa:N", alt.Tooltip("Resultado:Q", format=",.2f", title="R$")])
st.altair_chart(barras.properties(height=60 + 32 * len(empresas)), use_container_width=True)

# ── Tabela detalhada ──────────────────────────────────────────────────────────
st.divider()
st.subheader("📋 Tabela")
tdf = pd.DataFrame(dados)
tdf["Margem"] = tdf.apply(
    lambda r: (r["Resultado"] / r["Entrou"] * 100) if r["Entrou"] else 0, axis=1)
st.dataframe(
    tdf, hide_index=True, use_container_width=True,
    column_config={
        "Entrou": st.column_config.NumberColumn(format="R$ %.2f"),
        "Saiu": st.column_config.NumberColumn(format="R$ %.2f"),
        "Resultado": st.column_config.NumberColumn(format="R$ %.2f"),
        "Margem": st.column_config.NumberColumn("Margem", format="%.1f%%",
                                                help="Resultado ÷ Faturamento"),
    })
st.caption("Resultado = Faturamento − Despesa real. Não é o lucro contábil "
           "(falta depreciação, impostos sobre lucro etc.), mas mostra se a "
           "operação de cada empresa sobrou ou faltou caixa no período.")

# ── 🏦 Por conta — onde está o caixa ──────────────────────────────────────────
# Aqui entra TUDO que movimentou a conta (inclusive transferências entre as
# contas do grupo e aplicações), porque o objetivo é mostrar para onde o
# dinheiro foi fisicamente — não o resultado de competência.
st.divider()
st.subheader("🏦 Por conta — onde está o caixa")

cmov = query(
    """SELECT c.id cid, e.apelido emp, c.banco, c.descricao,
              COALESCE(SUM(CASE WHEN l.tipo='entrada' THEN l.valor ELSE 0 END),0) entrou,
              COALESCE(SUM(CASE WHEN l.tipo='saida'   THEN l.valor ELSE 0 END),0) saiu
       FROM lancamentos l
       JOIN contas_bancarias c ON c.id=l.conta_bancaria_id
       JOIN empresas e ON e.id=c.empresa_id
       WHERE l.data BETWEEN ? AND ?
       GROUP BY c.id, e.apelido, c.banco, c.descricao""", par)

if not cmov:
    st.info("Sem movimento por conta no período.")
else:
    contas = [{
        "Empresa": r["emp"],
        "Conta": f'{r["banco"]} · {r["descricao"]}',
        "Entrou": r["entrou"],
        "Saiu": r["saiu"],
        "Caixa do período": r["entrou"] - r["saiu"],
    } for r in cmov]
    contas.sort(key=lambda c: -c["Caixa do período"])
    ordem_contas = [c["Conta"] for c in contas]
    tot_caixa = sum(c["Caixa do período"] for c in contas)

    st.caption(
        "Movimento **real** de cada conta no período: entra tudo (vendas, "
        "transferências de outras contas do grupo, aplicações). "
        "🔴 **negativo = a conta gastou/transferiu mais do que recebeu** — foi "
        "bancada por outras contas (efeito do caixa centralizado). "
        "🟢 positivo = a conta segurou o dinheiro. "
        "É a *variação* do período, não o saldo absoluto (os extratos não trazem "
        "saldo inicial de todas as contas)."
    )

    cdf = pd.DataFrame([{"Conta": c["Conta"], "Caixa do período": c["Caixa do período"],
                         "Empresa": c["Empresa"]} for c in contas])
    barras_c = (
        alt.Chart(cdf)
        .mark_bar(cornerRadiusEnd=3)
        .encode(
            x=alt.X("Caixa do período:Q", title=None, axis=alt.Axis(format="~s")),
            y=alt.Y("Conta:N", sort=ordem_contas, title=None),
            color=alt.condition(alt.datum["Caixa do período"] >= 0,
                                 alt.value(VERDE), alt.value(VERMELHO)),
            tooltip=["Empresa:N", "Conta:N",
                     alt.Tooltip("Caixa do período:Q", format=",.2f", title="R$")],
        )
        .properties(height=60 + 32 * len(contas))
    )
    st.altair_chart(barras_c, use_container_width=True)

    st.dataframe(
        pd.DataFrame(contas), hide_index=True, use_container_width=True,
        column_config={
            "Entrou": st.column_config.NumberColumn(format="R$ %.2f"),
            "Saiu": st.column_config.NumberColumn(format="R$ %.2f"),
            "Caixa do período": st.column_config.NumberColumn(
                format="R$ %.2f", help="Entrou − Saiu (tudo, inclusive transferências internas)"),
        })
    st.caption(
        f"Soma do caixa de todas as contas no período: **{brl(tot_caixa)}**. "
        f"Esse número é o dinheiro que **de fato** circulou e ficou — diferente do "
        f"resultado de competência ({brl(tot_r)}), que conta a venda/despesa pela "
        f"data do fato, não pela hora que o dinheiro entrou ou saiu da conta. "
        f"A diferença vai para aplicações e dinheiro ainda a receber/pagar."
    )
