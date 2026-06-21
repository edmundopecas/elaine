"""
Fluxo de Caixa — realizado (extratos) + previsto (contas a pagar/receber).
Mostra a evolução do saldo dia a dia e a projeção com os títulos em aberto.
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from db import init_db, query

st.set_page_config(page_title="Fluxo de Caixa", page_icon="📈", layout="wide")
init_db()
st.title("📈 Fluxo de Caixa")

empresas = query("SELECT id, apelido FROM empresas WHERE ativa=1 ORDER BY apelido")
c1, c2, c3 = st.columns(3)
with c1:
    op = ["Todas as empresas"] + [e["apelido"] for e in empresas]
    sel = st.selectbox("Empresa", op)
    emp_id = None if sel == "Todas as empresas" else next(e["id"] for e in empresas if e["apelido"] == sel)
with c2:
    ini = st.date_input("De", value=date.today().replace(day=1))
with c3:
    fim = st.date_input("Até (inclui projeção)", value=date.today().replace(day=28))

filtro_emp = "AND empresa_id = ?" if emp_id else ""
p_emp = (emp_id,) if emp_id else ()

# ── Realizado (extratos) por dia ─────────────────────────────────────────────
realizado = query(
    f"""SELECT data,
               SUM(CASE WHEN tipo='entrada' THEN valor ELSE 0 END) entradas,
               SUM(CASE WHEN tipo='saida'   THEN valor ELSE 0 END) saidas
        FROM lancamentos
        WHERE data BETWEEN ? AND ? {filtro_emp}
        GROUP BY data ORDER BY data""",
    (ini.isoformat(), fim.isoformat(), *p_emp),
)

# ── Previsto (títulos em aberto) por vencimento ──────────────────────────────
previsto = query(
    f"""SELECT vencimento data,
               SUM(CASE WHEN tipo='receber' THEN valor ELSE 0 END) a_receber,
               SUM(CASE WHEN tipo='pagar'   THEN valor ELSE 0 END) a_pagar
        FROM titulos
        WHERE status='aberto' AND vencimento BETWEEN ? AND ? {filtro_emp}
        GROUP BY vencimento ORDER BY vencimento""",
    (ini.isoformat(), fim.isoformat(), *p_emp),
)

if not realizado and not previsto:
    st.info("Sem dados no período. Importe extratos ou cadastre contas a pagar/receber.")
    st.stop()

# Junta tudo num DataFrame diário
dias: dict[str, dict] = {}
for r in realizado:
    dias.setdefault(r["data"], {})["entradas"] = r["entradas"]
    dias[r["data"]]["saidas"] = r["saidas"]
for p in previsto:
    dias.setdefault(p["data"], {})["a_receber"] = p["a_receber"]
    dias[p["data"]]["a_pagar"] = p["a_pagar"]

df = pd.DataFrame([
    {"Data": d,
     "Entradas (real)": v.get("entradas", 0) or 0,
     "Saídas (real)": v.get("saidas", 0) or 0,
     "A receber (prev)": v.get("a_receber", 0) or 0,
     "A pagar (prev)": v.get("a_pagar", 0) or 0}
    for d, v in sorted(dias.items())
])
df["Líquido do dia"] = (df["Entradas (real)"] + df["A receber (prev)"]
                        - df["Saídas (real)"] - df["A pagar (prev)"])
df["Saldo acumulado"] = df["Líquido do dia"].cumsum()

tot_ent = df["Entradas (real)"].sum()
tot_sai = df["Saídas (real)"].sum()
tot_rec = df["A receber (prev)"].sum()
tot_pag = df["A pagar (prev)"].sum()

m1, m2, m3, m4 = st.columns(4)
m1.metric("Realizado — entradas", f"R$ {tot_ent:,.2f}")
m2.metric("Realizado — saídas", f"R$ {tot_sai:,.2f}")
m3.metric("A receber (previsto)", f"R$ {tot_rec:,.2f}")
m4.metric("A pagar (previsto)", f"R$ {tot_pag:,.2f}")

st.subheader("Saldo acumulado no período")
st.line_chart(df.set_index("Data")["Saldo acumulado"])

st.subheader("Detalhe diário")
st.dataframe(
    df.assign(**{c: df[c].map(lambda x: f"R$ {x:,.2f}")
                 for c in df.columns if c != "Data"}),
    use_container_width=True, hide_index=True,
)

with st.expander("ℹ️ Ver detalhes do cálculo"):
    st.markdown(
        "- **Realizado** vem dos extratos importados (entradas e saídas efetivas).\n"
        "- **Previsto** vem dos títulos em aberto em Contas a Pagar/Receber, "
        "alocados na data de vencimento.\n"
        "- **Líquido do dia** = (Entradas + A receber) − (Saídas + A pagar).\n"
        "- **Saldo acumulado** soma o líquido dia a dia (não inclui saldo "
        "inicial de banco — é a variação no período)."
    )
