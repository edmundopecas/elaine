"""
Cadastros — empresas do grupo, contas bancárias, centros de custo, plano de
contas e regras de classificação. É aqui que o Filipe cadastra o grupo todo.
"""
from __future__ import annotations

import streamlit as st

from db import execute, init_db, query

st.set_page_config(page_title="Cadastros", page_icon="🏢", layout="wide")
init_db()
st.title("🏢 Cadastros")

aba_emp, aba_contas, aba_centros, aba_plano, aba_regras = st.tabs(
    ["Empresas", "Contas bancárias", "Centros de custo", "Plano de contas", "Regras"]
)

# ── Empresas ─────────────────────────────────────────────────────────────────
with aba_emp:
    with st.form("nova_empresa"):
        c1, c2, c3 = st.columns(3)
        razao = c1.text_input("Razão social")
        apelido = c2.text_input("Apelido (nome curto)")
        cnpj = c3.text_input("CNPJ (opcional)")
        if st.form_submit_button("Adicionar empresa", type="primary"):
            if razao and apelido:
                execute("INSERT INTO empresas (razao_social, apelido, cnpj) VALUES (?,?,?)",
                        (razao, apelido, cnpj or None))
                st.success(f"Empresa '{apelido}' adicionada."); st.rerun()
            else:
                st.error("Razão social e apelido são obrigatórios.")
    st.dataframe(query("SELECT apelido, razao_social, cnpj, "
                       "CASE ativa WHEN 1 THEN 'ativa' ELSE 'inativa' END status "
                       "FROM empresas ORDER BY apelido"),
                 use_container_width=True, hide_index=True)

# ── Contas bancárias ─────────────────────────────────────────────────────────
with aba_contas:
    empresas = query("SELECT id, apelido FROM empresas WHERE ativa=1 ORDER BY apelido")
    if empresas:
        with st.form("nova_conta"):
            c1, c2, c3 = st.columns(3)
            emp = c1.selectbox("Empresa", [e["apelido"] for e in empresas])
            banco = c2.text_input("Banco")
            desc = c3.text_input("Descrição (ex: C/C 12345-6)")
            if st.form_submit_button("Adicionar conta", type="primary"):
                emp_id = next(e["id"] for e in empresas if e["apelido"] == emp)
                if banco:
                    execute("INSERT INTO contas_bancarias (empresa_id, banco, descricao) "
                            "VALUES (?,?,?)", (emp_id, banco, desc or None))
                    st.success("Conta adicionada."); st.rerun()
                else:
                    st.error("Informe o banco.")
    st.dataframe(query("SELECT e.apelido empresa, c.banco, c.descricao "
                       "FROM contas_bancarias c JOIN empresas e ON e.id=c.empresa_id "
                       "WHERE c.ativa=1 ORDER BY e.apelido"),
                 use_container_width=True, hide_index=True)

# ── Centros de custo ─────────────────────────────────────────────────────────
with aba_centros:
    with st.form("novo_centro"):
        nome = st.text_input("Novo centro de custo")
        if st.form_submit_button("Adicionar", type="primary"):
            if nome:
                execute("INSERT INTO centros_custo (nome) VALUES (?)", (nome,))
                st.success("Centro adicionado."); st.rerun()
    st.dataframe(query("SELECT nome FROM centros_custo WHERE ativo=1 ORDER BY nome"),
                 use_container_width=True, hide_index=True)

# ── Plano de contas ──────────────────────────────────────────────────────────
with aba_plano:
    st.caption("As categorias que formam a DRE. 'Entra DRE = não' são "
               "movimentações internas (não contam como resultado).")
    with st.form("nova_conta_plano"):
        c1, c2, c3, c4 = st.columns(4)
        nome = c1.text_input("Nome da categoria")
        grupo = c2.text_input("Grupo")
        tipo = c3.selectbox("Tipo", ["despesa", "receita", "transferencia"])
        entra = c4.selectbox("Entra na DRE?", ["sim", "não"])
        if st.form_submit_button("Adicionar categoria", type="primary"):
            if nome and grupo:
                maxordem = query("SELECT COALESCE(MAX(ordem),0)+1 o FROM plano_contas")[0]["o"]
                execute("INSERT INTO plano_contas (nome, grupo, tipo, entra_dre, ordem) "
                        "VALUES (?,?,?,?,?)",
                        (nome, grupo, tipo, 1 if entra == "sim" else 0, maxordem))
                st.success("Categoria adicionada."); st.rerun()
    st.dataframe(query("SELECT grupo, nome, tipo, "
                       "CASE entra_dre WHEN 1 THEN 'sim' ELSE 'não' END entra_dre "
                       "FROM plano_contas WHERE ativo=1 ORDER BY ordem"),
                 use_container_width=True, hide_index=True)

# ── Regras de classificação ──────────────────────────────────────────────────
with aba_regras:
    st.caption("Regras que classificam os extratos automaticamente. "
               "Você também cria regras direto na tela Classificar.")
    regras = query(
        "SELECT r.id, r.padrao, r.aplica_tipo, p.nome categoria, c.nome centro, "
        "r.vezes_aplicada, e.apelido empresa "
        "FROM regras_classificacao r "
        "LEFT JOIN plano_contas p ON p.id=r.plano_conta_id "
        "LEFT JOIN centros_custo c ON c.id=r.centro_custo_id "
        "LEFT JOIN empresas e ON e.id=r.empresa_id "
        "WHERE r.ativa=1 ORDER BY r.vezes_aplicada DESC"
    )
    if regras:
        st.dataframe(
            [{"Padrão": r["padrao"], "Tipo": r["aplica_tipo"] or "ambos",
              "Empresa": r["empresa"] or "todas", "Categoria": r["categoria"],
              "Centro": r["centro"] or "—", "Vezes aplicada": r["vezes_aplicada"]}
             for r in regras],
            use_container_width=True, hide_index=True,
        )
        rid = st.number_input("ID da regra para desativar", min_value=0, step=1)
        if st.button("Desativar regra") and rid:
            execute("UPDATE regras_classificacao SET ativa=0 WHERE id=?", (int(rid),))
            st.success("Regra desativada."); st.rerun()
    else:
        st.info("Nenhuma regra ainda. Elas nascem quando você classifica lançamentos.")
