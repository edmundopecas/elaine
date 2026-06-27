"""Empréstimos — quanto está sendo pago de empréstimo/financiamento no banco,
por VALOR e por BANCO. Junta a categoria Empréstimos/Financiamentos + qualquer
histórico de empréstimo/amortização/capital de giro (exclui os "Pessoal -
Financiamento", que são financiamentos particulares dos sócios).
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from db import query

st.title("🏦 Empréstimos — quanto está sendo pago no banco")
st.caption("Pagamentos de empréstimo/financiamento (amortização, capital de giro, "
           "débito de empréstimo). Não inclui financiamento particular dos sócios.")

# ── Detecção de empréstimo (mesma regra em todas as consultas) ────────────────
FILTRO = """l.tipo='saida' AND (
   l.plano_conta_id=39
   OR LOWER(COALESCE(l.descricao,'')) LIKE '%emprestimo%'
   OR LOWER(COALESCE(l.descricao,'')) LIKE '%amortiz%'
   OR LOWER(COALESCE(l.descricao,'')) LIKE '%cap giro%'
   OR LOWER(COALESCE(l.descricao,'')) LIKE '%capital de giro%'
   OR LOWER(COALESCE(l.descricao,'')) LIKE '%consignado%'
) AND COALESCE(p.nome,'') NOT LIKE 'Pessoal%'"""
JOIN = ("FROM lancamentos l "
        "LEFT JOIN plano_contas p ON p.id=l.plano_conta_id "
        "LEFT JOIN contas_bancarias cb ON cb.id=l.conta_bancaria_id "
        "LEFT JOIN empresas e ON e.id=l.empresa_id")


def brl(v) -> str:
    return f"R$ {float(v or 0):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


# ── Filtros: empresa e período ───────────────────────────────────────────────
empresas = query("SELECT id, apelido FROM empresas WHERE ativa=1 ORDER BY apelido")
ext = query(f"SELECT MIN(l.data) lo, MAX(l.data) hi {JOIN} WHERE {FILTRO}")[0]
lo = pd.to_datetime(ext["lo"]).date() if ext["lo"] else date.today()
hi = pd.to_datetime(ext["hi"]).date() if ext["hi"] else date.today()

c1, c2 = st.columns([1, 2])
sel_emp = c1.selectbox("Empresa", ["Todas"] + [e["apelido"] for e in empresas])
periodo = c2.date_input("Período (de / até)", value=(lo, hi),
                        min_value=lo, max_value=hi, format="DD/MM/YYYY")
if isinstance(periodo, (list, tuple)):
    d_ini, d_fim = (periodo[0], periodo[-1]) if periodo else (lo, hi)
else:
    d_ini = d_fim = periodo

cond = [FILTRO, "l.data BETWEEN ? AND ?"]
params: list = [d_ini.isoformat(), d_fim.isoformat()]
if sel_emp != "Todas":
    cond.append("l.empresa_id=?")
    params.append(next(e["id"] for e in empresas if e["apelido"] == sel_emp))
where = " AND ".join(cond)

# ── KPIs ─────────────────────────────────────────────────────────────────────
tot = query(f"SELECT COALESCE(SUM(l.valor),0) v, COUNT(*) n {JOIN} WHERE {where}", tuple(params))[0]
pend = query(f"SELECT COALESCE(SUM(l.valor),0) v, COUNT(*) n {JOIN} WHERE {where} AND l.classificado=0",
             tuple(params))[0]
m1, m2, m3 = st.columns(3)
m1.metric("💰 Total de empréstimos pagos", brl(tot["v"]), f"{tot['n']} pagamentos", delta_color="off")
m2.metric("✅ Já classificado", brl(tot["v"] - pend["v"]), delta_color="off")
m3.metric("❓ Pendente", brl(pend["v"]), f"{pend['n']} itens", delta_color="off")

st.divider()

# ── Por banco ────────────────────────────────────────────────────────────────
st.subheader("🏦 Por banco / conta")
por_banco = query(
    f"""SELECT COALESCE(cb.descricao, cb.banco, '—') AS "Banco / Conta",
        COALESCE(e.apelido,'—') AS "Empresa", COUNT(*) AS "Qtd",
        SUM(l.valor) AS "Valor"
        {JOIN} WHERE {where}
        GROUP BY cb.descricao, cb.banco, e.apelido ORDER BY SUM(l.valor) DESC""",
    tuple(params))
df_b = pd.DataFrame(por_banco)
if not df_b.empty:
    st.bar_chart(df_b.set_index("Banco / Conta")["Valor"], color="#7E9576", horizontal=True)
    df_b["Valor"] = df_b["Valor"].map(brl)
    st.dataframe(df_b, use_container_width=True, hide_index=True)

# ── Por categoria ────────────────────────────────────────────────────────────
st.subheader("📂 Por categoria")
por_cat = query(
    f"""SELECT COALESCE(p.nome,'— PENDENTE —') AS "Categoria", COUNT(*) AS "Qtd",
        SUM(l.valor) AS "Valor"
        {JOIN} WHERE {where} GROUP BY p.nome ORDER BY SUM(l.valor) DESC""",
    tuple(params))
df_c = pd.DataFrame(por_cat)
if not df_c.empty:
    df_c["Valor"] = df_c["Valor"].map(brl)
    st.dataframe(df_c, use_container_width=True, hide_index=True)

# ── Detalhe (cada pagamento) ─────────────────────────────────────────────────
st.divider()
st.subheader("📋 Cada pagamento")
det = query(
    f"""SELECT l.data AS "Data", COALESCE(cb.descricao, cb.banco, '—') AS "Banco",
        COALESCE(e.apelido,'—') AS "Empresa",
        COALESCE(l.contraparte, l.descricao, '—') AS "Descrição",
        l.valor AS "Valor", COALESCE(p.nome,'— pendente —') AS "Categoria"
        {JOIN} WHERE {where} ORDER BY l.valor DESC""",
    tuple(params))
df_d = pd.DataFrame(det)
if df_d.empty:
    st.info("Nenhum empréstimo no período/filtro.")
else:
    st.caption(f"{len(df_d)} pagamentos:")
    show = df_d.copy()
    show["Valor"] = show["Valor"].map(brl)
    st.dataframe(show, use_container_width=True, hide_index=True)
    import io
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        df_d.to_excel(w, index=False, sheet_name="Emprestimos")
    st.download_button("⬇️ Exportar Excel", buf.getvalue(),
                       file_name=f"emprestimos_{d_ini.isoformat()}_a_{d_fim.isoformat()}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
