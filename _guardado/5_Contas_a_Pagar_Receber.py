"""
Contas a Pagar / Receber — o lado PREVISTO. Cadastro de títulos com vencimento,
listagem por status e baixa (marca como pago/recebido).
"""
from __future__ import annotations

from datetime import date

import streamlit as st

from db import execute, init_db, query

st.set_page_config(page_title="Contas a Pagar/Receber", page_icon="📅", layout="wide")
init_db()
st.title("📅 Contas a Pagar / Receber")

empresas = query("SELECT id, apelido FROM empresas WHERE ativa=1 ORDER BY apelido")
if not empresas:
    st.error("Cadastre uma empresa primeiro em **Cadastros**.")
    st.stop()
planos = query("SELECT id, nome, grupo FROM plano_contas WHERE ativo=1 ORDER BY ordem")
centros = query("SELECT id, nome FROM centros_custo WHERE ativo=1 ORDER BY nome")
emp_rotulo = {e["apelido"]: e["id"] for e in empresas}
plano_rotulo = {"— nenhum —": None} | {f"{p['grupo']} › {p['nome']}": p["id"] for p in planos}
centro_rotulo = {"— nenhum —": None} | {c["nome"]: c["id"] for c in centros}

aba_novo, aba_lista = st.tabs(["➕ Novo título", "📋 Títulos"])

with aba_novo:
    with st.form("novo_titulo"):
        c1, c2, c3 = st.columns(3)
        with c1:
            tipo = st.radio("Tipo", ["pagar", "receber"],
                            format_func=lambda x: "A pagar" if x == "pagar" else "A receber")
            emp = st.selectbox("Empresa", list(emp_rotulo.keys()))
        with c2:
            desc = st.text_input("Descrição")
            contraparte = st.text_input("Fornecedor / Cliente")
        with c3:
            valor = st.number_input("Valor (R$)", min_value=0.0, step=0.01, format="%.2f")
            venc = st.date_input("Vencimento", value=date.today())
        cat = st.selectbox("Categoria", list(plano_rotulo.keys()))
        centro = st.selectbox("Centro de custo", list(centro_rotulo.keys()))
        if st.form_submit_button("Salvar título", type="primary"):
            if not desc or valor <= 0:
                st.error("Informe ao menos descrição e valor.")
            else:
                execute(
                    "INSERT INTO titulos (empresa_id, tipo, descricao, contraparte, "
                    "plano_conta_id, centro_custo_id, valor, vencimento) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (emp_rotulo[emp], tipo, desc, contraparte or None,
                     plano_rotulo[cat], centro_rotulo[centro], valor, venc.isoformat()),
                )
                st.success("Título cadastrado.")

with aba_lista:
    c1, c2 = st.columns(2)
    f_tipo = c1.selectbox("Filtrar tipo", ["Todos", "pagar", "receber"],
                          format_func=lambda x: {"pagar": "A pagar", "receber": "A receber"}.get(x, x))
    f_status = c2.selectbox("Filtrar status", ["aberto", "Todos", "pago", "recebido", "cancelado"])

    cond, params = ["1=1"], []
    if f_tipo != "Todos":
        cond.append("t.tipo=?"); params.append(f_tipo)
    if f_status != "Todos":
        cond.append("t.status=?"); params.append(f_status)
    titulos = query(
        f"SELECT t.*, e.apelido FROM titulos t JOIN empresas e ON e.id=t.empresa_id "
        f"WHERE {' AND '.join(cond)} ORDER BY t.vencimento",
        tuple(params),
    )

    if not titulos:
        st.info("Nenhum título com esses filtros.")
    else:
        total = sum(t["valor"] for t in titulos)
        st.metric("Total filtrado", f"R$ {total:,.2f}")
        for t in titulos:
            atrasado = t["status"] == "aberto" and t["vencimento"] < date.today().isoformat()
            flag = "⚠️ ATRASADO" if atrasado else t["status"].upper()
            lado = "A pagar" if t["tipo"] == "pagar" else "A receber"
            with st.expander(f"{lado} · {t['vencimento']} · R$ {t['valor']:,.2f} · "
                             f"{t['descricao']} [{t['apelido']}] — {flag}"):
                st.write(f"**Contraparte:** {t['contraparte'] or '—'}")
                if t["status"] == "aberto":
                    novo_status = "pago" if t["tipo"] == "pagar" else "recebido"
                    if st.button(f"✅ Marcar como {novo_status}", key=f"baixa_{t['id']}"):
                        execute("UPDATE titulos SET status=?, data_baixa=? WHERE id=?",
                                (novo_status, date.today().isoformat(), t["id"]))
                        st.success(f"Título baixado como {novo_status}.")
                        st.rerun()
                else:
                    st.caption(f"Baixado em {t['data_baixa'] or '—'}.")
