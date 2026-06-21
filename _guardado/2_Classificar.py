"""
Classificar — resolve os lançamentos pendentes (empresa/categoria/centro) e,
opcionalmente, salva uma regra pra automatizar os próximos iguais.
"""
from __future__ import annotations

import streamlit as st

from classificador import criar_regra, reclassificar_pendentes
from db import execute, init_db, query

st.set_page_config(page_title="Classificar", page_icon="🏷️", layout="wide")
init_db()
st.title("🏷️ Classificar lançamentos")

# Atalho: reaplica regras existentes nos pendentes
colA, colB = st.columns([1, 3])
with colA:
    if st.button("🔄 Reaplicar regras"):
        n = reclassificar_pendentes()
        st.success(f"{n} lançamento(s) classificado(s) pelas regras.")

planos = query("SELECT id, nome, grupo, tipo FROM plano_contas WHERE ativo=1 ORDER BY ordem")
centros = query("SELECT id, nome FROM centros_custo WHERE ativo=1 ORDER BY nome")
plano_rotulo = {f"{p['grupo']} › {p['nome']}": p["id"] for p in planos}
centro_rotulo = {"— nenhum —": None} | {c["nome"]: c["id"] for c in centros}

pendentes = query(
    "SELECT l.id, l.data, l.descricao, l.valor, l.tipo, e.apelido, l.empresa_id "
    "FROM lancamentos l LEFT JOIN empresas e ON e.id=l.empresa_id "
    "WHERE l.classificado=0 ORDER BY l.data DESC LIMIT 50"
)

if not pendentes:
    st.success("🎉 Nenhum lançamento pendente. Tudo classificado!")
    st.stop()

st.caption(f"{len(pendentes)} pendente(s) (mostrando até 50). "
           "Classifique e, se quiser, marque para criar uma regra automática.")

for lanc in pendentes:
    sinal = "🟢" if lanc["tipo"] == "entrada" else "🔴"
    with st.expander(
        f"{sinal} {lanc['data']} · R$ {lanc['valor']:,.2f} · "
        f"{lanc['descricao'] or '(sem histórico)'} · [{lanc['apelido'] or '—'}]"
    ):
        with st.form(f"form_{lanc['id']}"):
            c1, c2 = st.columns(2)
            with c1:
                cat = st.selectbox("Categoria (plano de contas)",
                                   list(plano_rotulo.keys()), key=f"cat_{lanc['id']}")
            with c2:
                centro = st.selectbox("Centro de custo",
                                      list(centro_rotulo.keys()), key=f"cc_{lanc['id']}")

            criar = st.checkbox("Criar regra automática pra próximos iguais",
                                key=f"rule_{lanc['id']}")
            padrao = st.text_input(
                "Padrão da regra (texto que se repete no histórico)",
                value=(lanc["descricao"] or "")[:40], key=f"pat_{lanc['id']}",
                help="Ex.: 'ALUGUEL', 'TARIFA', 'PIX FULANO'. Casa quando o padrão "
                     "estiver contido no histórico.",
            )
            salvar = st.form_submit_button("Salvar classificação", type="primary")

            if salvar:
                plano_id = plano_rotulo[cat]
                centro_id = centro_rotulo[centro]
                execute(
                    "UPDATE lancamentos SET plano_conta_id=?, centro_custo_id=?, "
                    "classificado=1 WHERE id=?",
                    (plano_id, centro_id, lanc["id"]),
                )
                msg = "Classificado."
                if criar and padrao.strip():
                    criar_regra(padrao, plano_id, centro_id,
                                empresa_id=lanc["empresa_id"], aplica_tipo=lanc["tipo"])
                    n = reclassificar_pendentes()
                    msg += f" Regra criada — {n} outro(s) pendente(s) classificado(s)."
                st.success(msg)
                st.rerun()
