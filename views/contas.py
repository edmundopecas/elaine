"""Contas Bancárias — liga cada conta à sua empresa. Ao trocar a empresa de uma
conta, TODOS os lançamentos daquela conta são movidos junto (cascata). Útil
quando o extrato entra provisoriamente numa empresa e depois é reatrelado."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from db import execute, query

st.title("🏦 Contas Bancárias")
st.caption("Ligue cada conta à sua empresa. Ao trocar a empresa e salvar, "
           "todos os lançamentos daquela conta vão junto.")

empresas = query("SELECT id, apelido FROM empresas WHERE ativa=1 ORDER BY apelido")
if not empresas:
    st.error("Cadastre uma empresa antes.")
    st.stop()
ap2id = {e["apelido"]: e["id"] for e in empresas}
id2ap = {e["id"]: e["apelido"] for e in empresas}

contas = query(
    """SELECT cb.id, cb.banco, cb.descricao, cb.empresa_id,
              (SELECT COUNT(*) FROM lancamentos l WHERE l.conta_bancaria_id=cb.id) AS n,
              (SELECT COALESCE(SUM(valor),0) FROM lancamentos l
                 WHERE l.conta_bancaria_id=cb.id AND l.tipo='saida') AS saidas
       FROM contas_bancarias cb WHERE cb.ativa=1
       ORDER BY cb.banco, cb.descricao"""
)
if not contas:
    st.info("Nenhuma conta cadastrada ainda. Importe um extrato primeiro.")
    st.stop()

atual = {c["id"]: c["empresa_id"] for c in contas}
df = pd.DataFrame([{
    "conta_id": c["id"],
    "Banco": c["banco"],
    "Conta": c["descricao"],
    "Empresa": id2ap.get(c["empresa_id"], ""),
    "Lançamentos": c["n"],
    "Saídas (R$)": c["saidas"],
} for c in contas])

edit = st.data_editor(
    df, hide_index=True, use_container_width=True,
    disabled=["Banco", "Conta", "Lançamentos", "Saídas (R$)"],
    column_config={
        "conta_id": None,
        "Empresa": st.column_config.SelectboxColumn(
            "Empresa", options=list(ap2id.keys()), required=True),
        "Saídas (R$)": st.column_config.NumberColumn(format="R$ %.2f"),
    },
)

if st.button("💾 Salvar vínculos", type="primary"):
    mudancas = 0
    for _, row in edit.iterrows():
        nova = ap2id.get(row["Empresa"])
        if nova and nova != atual.get(int(row["conta_id"])):
            execute("UPDATE contas_bancarias SET empresa_id=? WHERE id=?",
                    (nova, int(row["conta_id"])))
            execute("UPDATE lancamentos SET empresa_id=? WHERE conta_bancaria_id=?",
                    (nova, int(row["conta_id"])))
            mudancas += 1
    if mudancas:
        st.success(f"{mudancas} conta(s) reatreladas — lançamentos movidos junto.")
        st.rerun()
    else:
        st.info("Nada alterado.")
