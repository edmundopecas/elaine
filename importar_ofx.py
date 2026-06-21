"""Importa um OFX para uma empresa do grupo (headless, espelha views/importar.py).

Uso:
    python importar_ofx.py <ofx> "<Empresa>" "<Banco>" "<Descrição da conta>"

Faz: backup do banco, cadastra a conta (idempotente), lê o OFX, classifica
(1) transferência interna por CNPJ do grupo, (2) movimento mecânico da conta
rendimento como Aplicação/Resgate (fora da DRE), (3) regras de-para; o resto fica
pendente. Grava com dedup por linha_hash e registra a importação.
"""
from __future__ import annotations

import shutil
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

from classificador import (classificar_movimento, empresa_do_grupo_por_cnpj,
                           id_categoria_transferencia_interna, regras_ativas)
from db import DB_PATH, execute, query, query_one
from dedup import planejar_insercao
from parsers import hash_arquivo, parse_extrato

# Históricos que são só dinheiro indo/voltando da conta rendimento (não é gasto):
# BB "BB Rende Fácil"; BTG "RESGATE/APLICAÇÃO CONTA REMUNERADA", "DÉBITO/CRÉDITO NA CONTA CORRENTE".
MECANICOS_APLICACAO = ("rende facil", "conta remunerada", "na conta corrente")


def _norm(s: str) -> str:
    return unicodedata.normalize("NFKD", s or "").encode("ASCII", "ignore").decode().lower()


def main(caminho: str, apelido: str, banco: str, conta_desc: str) -> None:
    arquivo = Path(caminho)
    file_bytes = arquivo.read_bytes()

    emp = query_one("SELECT id, apelido FROM empresas WHERE apelido = ?", (apelido,))
    if not emp:
        sys.exit(f"Empresa '{apelido}' não encontrada.")
    emp_id = emp["id"]

    bkp = Path(DB_PATH).with_name(f"elaine.backup-{datetime.now():%Y%m%d-%H%M%S}.db")
    shutil.copy2(DB_PATH, bkp)
    print(f"Backup: {bkp.name}")

    conta = query_one(
        "SELECT id FROM contas_bancarias WHERE empresa_id=? AND banco=? AND descricao=?",
        (emp_id, banco, conta_desc),
    )
    if conta:
        conta_id = conta["id"]
    else:
        conta_id = execute(
            "INSERT INTO contas_bancarias (empresa_id, banco, descricao, ativa) "
            "VALUES (?, ?, ?, 1)", (emp_id, banco, conta_desc),
        )
        print(f"Conta cadastrada: {banco} - {conta_desc} (id {conta_id}) -> {emp['apelido']}")

    movimentos = parse_extrato(file_bytes, arquivo.name)
    regras = regras_ativas()
    planos = {p["id"]: p["nome"] for p in query("SELECT id, nome FROM plano_contas")}
    cat_transf = id_categoria_transferencia_interna()
    cat_aplic = query_one(
        "SELECT id FROM plano_contas WHERE tipo='transferencia' AND nome LIKE '%plica%'"
    )
    cat_aplic_id = cat_aplic["id"] if cat_aplic else None

    for m in movimentos:
        h = _norm(m["historico"])
        interna = empresa_do_grupo_por_cnpj(m.get("cnpj_contraparte"))
        if interna and cat_transf:
            m["_plano_id"], m["_regra"], m["_classif"] = cat_transf, None, True
        elif cat_aplic_id and any(t in h for t in MECANICOS_APLICACAO):
            m["_plano_id"], m["_regra"], m["_classif"] = cat_aplic_id, None, True
        else:
            regra = classificar_movimento(m["historico"], m["tipo"], emp_id, regras)
            m["_regra"] = regra
            m["_plano_id"] = regra["plano_conta_id"] if regra else None
            m["_classif"] = bool(regra)

    imp_id = execute(
        "INSERT INTO importacoes (empresa_id, conta_bancaria_id, arquivo_nome, "
        "arquivo_hash, formato, linhas_total) VALUES (?, ?, ?, ?, ?, ?)",
        (emp_id, conta_id, arquivo.name, hash_arquivo(file_bytes),
         arquivo.name.rsplit(".", 1)[-1], len(movimentos)),
    )

    # dedup por multiset de conteúdo (não colapsa parcelas idênticas — ver dedup.py)
    existentes = [dict(r) for r in query(
        "SELECT data, valor, tipo, documento, descricao, linha_hash "
        "FROM lancamentos WHERE conta_bancaria_id=?", (conta_id,))]
    a_inserir, duplicados = planejar_insercao(movimentos, existentes)

    importados = 0
    for m, linha_hash in a_inserir:
        execute(
            "INSERT INTO lancamentos (empresa_id, conta_bancaria_id, data, descricao, "
            "contraparte, cnpj_contraparte, documento, valor, tipo, plano_conta_id, "
            "centro_custo_id, classificado, origem, regra_id, importacao_id, "
            "linha_hash, saldo_apos) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (emp_id, conta_id, m["data"].isoformat(), m["historico"],
             m.get("contraparte"), m.get("cnpj_contraparte"), m["documento"],
             m["valor"], m["tipo"], m["_plano_id"], None,
             1 if m["_classif"] else 0, "extrato",
             m["_regra"]["id"] if m["_regra"] else None, imp_id,
             linha_hash, m["saldo_apos"]),
        )
        if m["_regra"]:
            execute("UPDATE regras_classificacao SET vezes_aplicada=vezes_aplicada+1 "
                    "WHERE id=?", (m["_regra"]["id"],))
        importados += 1

    execute("UPDATE importacoes SET linhas_importadas=?, linhas_duplicadas=? WHERE id=?",
            (importados, duplicados, imp_id))

    print(f"\nImportado em {emp['apelido']}: {importados} novos · {duplicados} duplicados.")
    print("\n=== SAÍDAS importadas ===")
    for m in sorted((x for x in movimentos if x["tipo"] == "saida"), key=lambda x: -x["valor"]):
        cat = planos.get(m["_plano_id"]) or "— PENDENTE —"
        cp = m.get("contraparte") or "(sem contraparte)"
        print(f"  {m['data']}  R$ {m['valor']:>10,.2f}  {cp:34.34}  -> {cat}")


if __name__ == "__main__":
    if len(sys.argv) < 5:
        sys.exit('Uso: python importar_ofx.py <ofx> "<Empresa>" "<Banco>" "<Conta>"')
    main(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4])
