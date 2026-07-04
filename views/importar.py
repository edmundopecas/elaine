"""Importar Extrato — lê OFX / CSV / **Excel** (BB, Santander, Asaas: detecta o
banco sozinho), classifica (transferência interna por CNPJ, aplicação/resgate
mecânica, regras de-para) e grava com **dedup à prova de reimport** (por multiset
data+valor+tipo — reimportar o mesmo período não duplica)."""
from __future__ import annotations

import unicodedata

import streamlit as st

from classificador import (classificar_movimento, empresa_do_grupo_por_cnpj,
                           id_categoria_transferencia_interna, regras_ativas)
from db import execute, query, query_one
from dedup import planejar_insercao
from parsers import detectar_banco_xlsx, hash_arquivo, parse_extrato

# Históricos que são só dinheiro indo/voltando de conta rendimento (não é gasto):
MECANICOS_APLICACAO = ("rende facil", "conta remunerada", "na conta corrente")


def _norm(s: str) -> str:
    return unicodedata.normalize("NFKD", s or "").encode("ASCII", "ignore").decode().lower()


st.title("📥 Importar Extrato")
st.caption("Aceita **OFX**, **CSV** e **Excel** (Banco do Brasil, Santander, Asaas — "
           "reconheço o banco sozinho). Reimportar o mesmo período é seguro: não duplica.")

empresas = query("SELECT id, apelido FROM empresas WHERE ativa = 1 ORDER BY apelido")
if not empresas:
    st.error("Cadastre ao menos uma empresa antes de importar.")
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

arquivo = st.file_uploader("Arquivo do extrato (OFX, CSV ou Excel)",
                           type=["csv", "txt", "ofx", "qfx", "xlsx", "xls"])

if arquivo:
    file_bytes = arquivo.read()
    ext = arquivo.name.lower().rsplit(".", 1)[-1]
    try:
        if ext in ("xlsx", "xls"):
            banco = detectar_banco_xlsx(file_bytes)
            if banco:
                st.info(f"🔎 Detectei que é do **{banco}**.")
        movimentos = parse_extrato(file_bytes, arquivo.name)
    except Exception as e:
        st.error(f"Não consegui ler o arquivo: {e}")
        st.stop()

    if not movimentos:
        st.warning("Nenhum movimento encontrado no arquivo.")
        st.stop()

    # ── Pré-classificação (preview, antes de gravar) ─────────────────────────
    #  1) transferência interna se o CNPJ da contraparte é de OUTRA empresa;
    #  2) aplicação/resgate mecânica (conta rendimento) → fora da DRE;
    #  3) senão, aplica as regras de-para; o resto fica pendente.
    regras = regras_ativas()
    planos = {p["id"]: p["nome"] for p in query("SELECT id, nome FROM plano_contas")}
    cat_transf = id_categoria_transferencia_interna()
    cat_aplic = query_one("SELECT id FROM plano_contas WHERE tipo='transferencia' "
                          "AND nome LIKE '%plica%'")
    cat_aplic_id = cat_aplic["id"] if cat_aplic else None

    for m in movimentos:
        interna = empresa_do_grupo_por_cnpj(m.get("cnpj_contraparte"))
        h = _norm(m["historico"])
        if interna and interna["id"] != emp_id and cat_transf:
            m["_plano_id"], m["_centro_id"], m["_regra"] = cat_transf, None, None
            m["_classificado"] = True
            m["_categoria"] = f"↔ Interna ({interna['apelido']})"
        elif cat_aplic_id and any(t in h for t in MECANICOS_APLICACAO):
            m["_plano_id"], m["_centro_id"], m["_regra"] = cat_aplic_id, None, None
            m["_classificado"] = True
            m["_categoria"] = "Aplicação/Resgate (fora da DRE)"
        else:
            regra = classificar_movimento(m["historico"], m["tipo"], emp_id, regras)
            m["_regra"] = regra
            m["_plano_id"] = regra["plano_conta_id"] if regra else None
            m["_centro_id"] = regra["centro_custo_id"] if regra else None
            m["_classificado"] = bool(regra)
            m["_categoria"] = planos.get(regra["plano_conta_id"]) if regra else None

    # ── Dedup à prova de reimport (multiset data+valor+tipo por conta) ───────
    existentes = [dict(r) for r in query(
        "SELECT data, valor, tipo, documento, descricao, linha_hash "
        "FROM lancamentos WHERE conta_bancaria_id=?", (conta_id,))] if conta_id else []
    hashes_globais = {r["linha_hash"] for r in query(
        "SELECT linha_hash FROM lancamentos WHERE linha_hash IS NOT NULL")}
    a_inserir, duplicados = planejar_insercao(movimentos, existentes, hashes_globais)

    internas = sum(1 for m, _ in a_inserir if (m["_categoria"] or "").startswith("↔"))
    por_regra = sum(1 for m, _ in a_inserir if m["_regra"])
    pendentes = sum(1 for m, _ in a_inserir if not m["_classificado"])
    st.success(f"**{len(movimentos)}** movimentos lidos · **{len(a_inserir)}** novos "
               f"({internas} transferências internas · {por_regra} por regras · "
               f"{pendentes} ficarão pendentes) · **{duplicados}** já estavam no sistema.")

    st.dataframe(
        [{"Data": m["data"], "Tipo": m["tipo"], "Valor": f"R$ {m['valor']:,.2f}",
          "Contraparte": m.get("contraparte") or "—",
          "Histórico": (m["historico"] or "")[:60],
          "Categoria (auto)": m["_categoria"] or "— pendente —"}
         for m, _ in a_inserir],
        use_container_width=True, hide_index=True,
    )

    if not a_inserir:
        st.info("Nada novo pra importar — esse período já está todo no sistema.")
        st.stop()

    if st.button("✅ Confirmar importação", type="primary"):
        arq_hash = hash_arquivo(file_bytes)
        imp_id = execute(
            "INSERT INTO importacoes (empresa_id, conta_bancaria_id, arquivo_nome, "
            "arquivo_hash, formato, linhas_total) VALUES (?, ?, ?, ?, ?, ?)",
            (emp_id, conta_id, arquivo.name, arq_hash, ext, len(movimentos)),
        )
        origem = "extrato-xls" if ext in ("xlsx", "xls") else "extrato"
        for m, lh in a_inserir:
            execute(
                "INSERT INTO lancamentos (empresa_id, conta_bancaria_id, data, descricao, "
                "contraparte, cnpj_contraparte, documento, valor, tipo, plano_conta_id, "
                "centro_custo_id, classificado, origem, regra_id, importacao_id, "
                "linha_hash, saldo_apos) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (emp_id, conta_id, m["data"].isoformat(), m["historico"],
                 m.get("contraparte"), m.get("cnpj_contraparte"), m["documento"],
                 m["valor"], m["tipo"], m["_plano_id"], m["_centro_id"],
                 1 if m["_classificado"] else 0, origem,
                 m["_regra"]["id"] if m["_regra"] else None, imp_id,
                 lh, m["saldo_apos"]),
            )
            if m["_regra"]:
                execute("UPDATE regras_classificacao SET vezes_aplicada=vezes_aplicada+1 "
                        "WHERE id=?", (m["_regra"]["id"],))

        execute("UPDATE importacoes SET linhas_importadas=?, linhas_duplicadas=? WHERE id=?",
                (len(a_inserir), duplicados, imp_id))
        st.success(f"Importado! {len(a_inserir)} novos · {duplicados} duplicados (ignorados).")
        st.rerun()
