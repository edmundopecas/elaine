"""Importação pontual: extrato BB (ag 3393 / c 44653) -> empresa Supernova.

Espelha o fluxo de views/importar.py (dedup por linha_hash, registro em
importacoes, aplica regras + detecção de transferência interna por CNPJ).
Extra: classifica "BB Rende Fácil" como Aplicação/Resgate (mecânico, fora da DRE).
Roda uma vez: python import_bb_supernova.py <caminho_ofx>
"""
from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path

from classificador import (classificar_movimento, empresa_do_grupo_por_cnpj,
                           id_categoria_transferencia_interna, regras_ativas)
from db import DB_PATH, execute, query, query_one
from parsers import hash_arquivo, parse_extrato

EMPRESA_APELIDO = "Supernova"
BANCO = "Banco do Brasil"
CONTA_DESC = "BB C/C 3393 / 44653"


def _norm(s: str) -> str:
    import unicodedata as u
    return u.normalize("NFKD", s or "").encode("ASCII", "ignore").decode().lower()


def main(caminho: str) -> None:
    arquivo = Path(caminho)
    file_bytes = arquivo.read_bytes()

    emp = query_one("SELECT id, apelido FROM empresas WHERE apelido = ?", (EMPRESA_APELIDO,))
    if not emp:
        sys.exit(f"Empresa '{EMPRESA_APELIDO}' não encontrada.")
    emp_id = emp["id"]

    # backup antes de qualquer mutação
    bkp = Path(DB_PATH).with_name(f"elaine.backup-{datetime.now():%Y%m%d-%H%M%S}.db")
    shutil.copy2(DB_PATH, bkp)
    print(f"Backup: {bkp.name}")

    # cadastra a conta BB da Supernova (idempotente)
    conta = query_one(
        "SELECT id FROM contas_bancarias WHERE empresa_id=? AND banco=? AND descricao=?",
        (emp_id, BANCO, CONTA_DESC),
    )
    if conta:
        conta_id = conta["id"]
    else:
        conta_id = execute(
            "INSERT INTO contas_bancarias (empresa_id, banco, descricao, ativa) "
            "VALUES (?, ?, ?, 1)", (emp_id, BANCO, CONTA_DESC),
        )
        print(f"Conta cadastrada: {BANCO} - {CONTA_DESC} (id {conta_id}) -> {emp['apelido']}")

    movimentos = parse_extrato(file_bytes, arquivo.name)
    regras = regras_ativas()
    planos = {p["id"]: p["nome"] for p in query("SELECT id, nome FROM plano_contas")}
    cat_transf = id_categoria_transferencia_interna()
    cat_aplic = query_one(
        "SELECT id FROM plano_contas WHERE tipo='transferencia' AND nome LIKE '%plica%'"
    )
    cat_aplic_id = cat_aplic["id"] if cat_aplic else None

    for m in movimentos:
        interna = empresa_do_grupo_por_cnpj(m.get("cnpj_contraparte"))
        # Só transferência se vier de OUTRA empresa do grupo; CNPJ próprio = venda (fix 23/06).
        if interna and interna["id"] != emp_id and cat_transf:
            m["_plano_id"], m["_regra"], m["_classif"] = cat_transf, None, True
        elif "rende facil" in _norm(m["historico"]) and cat_aplic_id:
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
             m["valor"], m["tipo"], m["_plano_id"], None,
             1 if m["_classif"] else 0, "extrato",
             m["_regra"]["id"] if m["_regra"] else None, imp_id,
             m["linha_hash"], m["saldo_apos"]),
        )
        if m["_regra"]:
            execute("UPDATE regras_classificacao SET vezes_aplicada=vezes_aplicada+1 "
                    "WHERE id=?", (m["_regra"]["id"],))
        importados += 1

    execute("UPDATE importacoes SET linhas_importadas=?, linhas_duplicadas=? WHERE id=?",
            (importados, duplicados, imp_id))

    print(f"\nImportado em {emp['apelido']}: {importados} novos · {duplicados} duplicados.")
    print("\n=== SAÍDAS importadas ===")
    for m in sorted((x for x in movimentos if x["tipo"] == "saida"),
                    key=lambda x: -x["valor"]):
        cat = planos.get(m["_plano_id"]) or "— PENDENTE —"
        cp = m.get("contraparte") or "(sem contraparte)"
        print(f"  {m['data']}  R$ {m['valor']:>10,.2f}  {cp:28.28}  -> {cat}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "")
