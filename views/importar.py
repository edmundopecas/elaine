"""Importar Extrato — lê CSV/OFX, detecta transferências internas pelo CNPJ da
contraparte, aplica as regras de classificação e grava (com dedup)."""
from __future__ import annotations

import streamlit as st

from classificador import (classificar_movimento, empresa_do_grupo_por_cnpj,
                           id_categoria_transferencia_interna, regras_ativas)
from db import execute, query, query_one
from parsers import hash_arquivo, parse_extrato

st.title("📥 Importar Extrato")

empresas = query("SELECT id, apelido FROM empresas WHERE ativa = 1 ORDER BY apelido")
if not empresas:
    st.error("Cadastre ao menos uma empresa em **Cadastros** antes de importar.")
    st.stop()

col1, col2 = st.columns(2)
with col1:
    emp_sel = st.selectbox("Empresa do extrato", [e["apelido"] for e in empresas])
    emp_id = next(e["id"] for e in empresas if e["apelido"] == emp_sel)
with col2:
    contas = query("SELECT id, banco, descricao FROM contas_bancarias "
                   "WHERE empresa_id=? AND ativa=1", (emp_id,))
    if contas:
        rotulo = {f"{c['banco']} — {c['descricao'] or ''}".strip(" —"): c["id"] for c in contas}
        conta_sel = st.selectbox("Conta bancária", list(rotulo.keys()))
        conta_id = rotulo[conta_sel]
    else:
        st.warning("Empresa sem conta bancária cadastrada (opcional).")
        conta_id = None

arquivo = st.file_uploader("Arquivo do extrato (CSV ou OFX)", type=["csv", "txt", "ofx", "qfx"])

if arquivo:
    file_bytes = arquivo.read()
    try:
        movimentos = parse_extrato(file_bytes, arquivo.name)
    except Exception as e:
        st.error(f"Não consegui ler o arquivo: {e}")
        st.stop()

    if not movimentos:
        st.warning("Nenhum movimento encontrado no arquivo.")
        st.stop()

    # Pré-classificação (preview, antes de gravar):
    #  1) transferência interna se o CNPJ da contraparte é do grupo;
    #  2) senão, aplica as regras de-para.
    regras = regras_ativas()
    planos = {p["id"]: p["nome"] for p in query("SELECT id, nome FROM plano_contas")}
    cat_transf = id_categoria_transferencia_interna()

    for m in movimentos:
        interna = empresa_do_grupo_por_cnpj(m.get("cnpj_contraparte"))
        # Só "Transferência entre Empresas" se vier de OUTRA empresa do grupo. CNPJ da
        # própria empresa da conta = venda no PIX da loja (chave PIX = CNPJ) — deixa as
        # regras decidirem, senão a venda some da DRE (fix 23/06).
        if interna and interna["id"] != emp_id and cat_transf:
            m["_plano_id"] = cat_transf
            m["_centro_id"] = None
            m["_regra"] = None
            m["_classificado"] = True
            m["_categoria"] = f"↔ Interna ({interna['apelido']})"
        else:
            regra = classificar_movimento(m["historico"], m["tipo"], emp_id, regras)
            m["_regra"] = regra
            m["_plano_id"] = regra["plano_conta_id"] if regra else None
            m["_centro_id"] = regra["centro_custo_id"] if regra else None
            m["_classificado"] = bool(regra)
            m["_categoria"] = planos.get(regra["plano_conta_id"]) if regra else None

    internas = sum(1 for m in movimentos if m["_categoria"] and m["_categoria"].startswith("↔"))
    por_regra = sum(1 for m in movimentos if m["_regra"])
    pendentes = sum(1 for m in movimentos if not m["_classificado"])
    st.success(f"**{len(movimentos)}** movimentos lidos · "
               f"**{internas}** transferências internas (detectadas pelo CNPJ) · "
               f"**{por_regra}** por regras · **{pendentes}** ficarão pendentes.")

    st.dataframe(
        [{"Data": m["data"], "Tipo": m["tipo"], "Valor": f"R$ {m['valor']:,.2f}",
          "Contraparte": m.get("contraparte") or "—",
          "Histórico": (m["historico"] or "")[:60],
          "Categoria (auto)": m["_categoria"] or "— pendente —"}
         for m in movimentos],
        use_container_width=True, hide_index=True,
    )

    if st.button("✅ Confirmar importação", type="primary"):
        arq_hash = hash_arquivo(file_bytes)
        imp_id = execute(
            "INSERT INTO importacoes (empresa_id, conta_bancaria_id, arquivo_nome, "
            "arquivo_hash, formato, linhas_total) VALUES (?, ?, ?, ?, ?, ?)",
            (emp_id, conta_id, arquivo.name, arq_hash,
             arquivo.name.rsplit(".", 1)[-1], len(movimentos)),
        )
        importados = duplicados = 0
        for m in movimentos:
            if query_one("SELECT 1 FROM lancamentos WHERE linha_hash=?", (m["linha_hash"],)):
                duplicados += 1
                continue
            execute(
                "INSERT INTO lancamentos (empresa_id, conta_bancaria_id, data, descricao, "
                "contraparte, cnpj_contraparte, documento, valor, tipo, plano_conta_id, "
                "centro_custo_id, classificado, origem, regra_id, importacao_id, "
                "linha_hash, saldo_apos) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (emp_id, conta_id, m["data"].isoformat(), m["historico"],
                 m.get("contraparte"), m.get("cnpj_contraparte"), m["documento"],
                 m["valor"], m["tipo"], m["_plano_id"], m["_centro_id"],
                 1 if m["_classificado"] else 0, "extrato",
                 m["_regra"]["id"] if m["_regra"] else None, imp_id,
                 m["linha_hash"], m["saldo_apos"]),
            )
            if m["_regra"]:
                execute("UPDATE regras_classificacao SET vezes_aplicada=vezes_aplicada+1 "
                        "WHERE id=?", (m["_regra"]["id"],))
            importados += 1

        execute("UPDATE importacoes SET linhas_importadas=?, linhas_duplicadas=? WHERE id=?",
                (importados, duplicados, imp_id))
        st.success(f"Importado! {importados} novos · {duplicados} duplicados (ignorados).")
        if duplicados:
            st.caption("Duplicados são linhas idênticas já importadas — "
                       "seguro reimportar o mesmo período.")
