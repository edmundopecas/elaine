"""
Base de Tipos (de-para) — o dicionário que o sistema usa pra saber "do que se
trata cada coisa". Cada regra diz: quando o histórico/fornecedor contém esse
**padrão**, a categoria é essa. Vale pra qualquer banco (casa pelo texto), e é
alimentada automaticamente quando você marca algo na tela de Saídas.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from classificador import criar_regra, reclassificar_pendentes
from db import execute, query

st.title("📒 Base de Tipos")
st.caption("Esse é o cérebro da classificação automática: **padrão → categoria**. "
           "O sistema usa ao importar extratos e ao você marcar Saídas. "
           "Edite, apague ou adicione — depois clique em Salvar.")

# Categorias disponíveis pro seletor
cats = query("SELECT id, nome FROM plano_contas WHERE ativo=1 ORDER BY tipo, ordem")
nome_para_id = {c["nome"]: c["id"] for c in cats}
id_para_nome = {v: k for k, v in nome_para_id.items()}

APLICA = {"ambos": None, "entrada": "entrada", "saída": "saida"}
APLICA_INV = {None: "ambos", "entrada": "entrada", "saida": "saída"}

regras = query(
    "SELECT r.id, r.padrao, r.plano_conta_id, r.aplica_tipo, r.vezes_aplicada "
    "FROM regras_classificacao r WHERE r.ativa=1 "
    "ORDER BY r.aplica_tipo, r.vezes_aplicada DESC, r.padrao")

st.markdown(f"**{len(regras)} regras** na base:")

df = pd.DataFrame([{
    "id": r["id"],
    "Padrão (texto no histórico)": r["padrao"],
    "O que é (categoria)": id_para_nome.get(r["plano_conta_id"], "—"),
    "Aplica em": APLICA_INV.get(r["aplica_tipo"], "ambos"),
    "Vezes usada": r["vezes_aplicada"],
    "Apagar": False,
} for r in regras])

edit = st.data_editor(
    df,
    column_config={
        "id": None,
        "Padrão (texto no histórico)": st.column_config.TextColumn(width="large"),
        "O que é (categoria)": st.column_config.SelectboxColumn(
            options=list(nome_para_id.keys()), width="medium", required=True),
        "Aplica em": st.column_config.SelectboxColumn(
            options=list(APLICA.keys()), width="small"),
        "Vezes usada": st.column_config.NumberColumn(disabled=True, width="small"),
        "Apagar": st.column_config.CheckboxColumn(width="small"),
    },
    hide_index=True, use_container_width=True, num_rows="fixed",
    key="editor_base", height=480,
)

if st.button("💾 Salvar base", type="primary"):
    antes = {r["id"]: r for _, r in df.iterrows()}
    apagadas = atualizadas = 0
    for _, row in edit.iterrows():
        rid = int(row["id"])
        if row["Apagar"]:
            execute("UPDATE regras_classificacao SET ativa=0 WHERE id=?", (rid,))
            apagadas += 1
            continue
        a = antes[rid]
        mudou = (row["Padrão (texto no histórico)"] != a["Padrão (texto no histórico)"]
                 or row["O que é (categoria)"] != a["O que é (categoria)"]
                 or row["Aplica em"] != a["Aplica em"])
        if mudou:
            execute("UPDATE regras_classificacao SET padrao=?, plano_conta_id=?, "
                    "aplica_tipo=? WHERE id=?",
                    (str(row["Padrão (texto no histórico)"]).strip(),
                     nome_para_id[row["O que é (categoria)"]],
                     APLICA[row["Aplica em"]], rid))
            atualizadas += 1
    auto = reclassificar_pendentes()
    st.success(f"Base salva! {atualizadas} alterada(s), {apagadas} desativada(s). "
               f"{auto} pendente(s) classificada(s) com as regras.")
    st.rerun()

# ── Adicionar nova regra manualmente ─────────────────────────────────────────
with st.expander("➕ Adicionar regra manual"):
    cpad, ccat, ctipo = st.columns([2, 2, 1])
    novo_padrao = cpad.text_input("Padrão (ex.: MG VIDROS)")
    nova_cat = ccat.selectbox("Categoria", list(nome_para_id.keys()), key="nova_cat")
    novo_tipo = ctipo.selectbox("Aplica em", list(APLICA.keys()), key="novo_tipo")
    if st.button("Adicionar"):
        if novo_padrao.strip():
            criar_regra(novo_padrao, nome_para_id[nova_cat], None,
                        None, APLICA[novo_tipo], "contem", 3)
            auto = reclassificar_pendentes()
            st.success(f"Regra adicionada! {auto} pendente(s) classificada(s).")
            st.rerun()
        else:
            st.warning("Informe o padrão.")
