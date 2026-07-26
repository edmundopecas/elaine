"""Importa o EXTRATO .xls do Safra (HTML disfarçado de Excel) para uma conta do grupo.

Fonte: o extrato oficial do Safra (`Extrato (N).xls`) — mais completo que o OFX (traz
o pagador no Complemento). Espelha a classificação do importar_ofx.py e adiciona uma
regra específica do Safra:

  * BOLETOS DDA são CONSOLIDADOS numa linha somada por dia (descricao "Pagamento De
    Boleto Dda", origem 'extrato') — idêntico ao que o OFX Safra produz. Isso vale
    tanto pros dias que o extrato já traz somados quanto pros que traz itemizados por
    valor (sem favorecido). Assim o `detalhar_dda_cli.py` consegue itemizar julho depois
    pelo relatório "Por Pagamentos" (que traz o favorecido) SEM duplicar — ele procura
    exatamente `origem='extrato' AND descricao LIKE '%BOLETO DDA%'`.

Dedup por multiset (data,valor,tipo) + hashes globais (dedup.py). Backup antes.
Preview por padrão; passe --commit pra gravar.

Uso: python importar_safra_xls.py <xls> "<Empresa>" "<Descrição da conta>" [--commit]
"""
from __future__ import annotations

import shutil
import sys
import unicodedata
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from classificador import (classificar_movimento, empresa_do_grupo_por_cnpj,
                           id_categoria_transferencia_interna, regras_ativas)
from db import DB_PATH, execute, query, query_one
from dedup import planejar_insercao
from parsers import _hash_mov, hash_arquivo, parse_extrato
from saldo_extrato import conferir, ler_saldos, registrar

MECANICOS_APLICACAO = ("rende facil", "conta remunerada", "na conta corrente")
BANCO = "Safra"


def _norm(s: str) -> str:
    return unicodedata.normalize("NFKD", s or "").encode("ASCII", "ignore").decode().lower()


def _eh_dda(m: dict) -> bool:
    return "boleto dda" in _norm(m.get("historico"))


def _consolidar_dda(movimentos: list[dict], emp_id: int) -> list[dict]:
    """Soma os boletos DDA por dia numa única linha 'Pagamento De Boleto Dda'.

    Deixa todo o resto intacto. A linha somada fica sem favorecido/CNPJ (é anônima,
    igual à do OFX) — vira pendente e é itemizada depois pelo relatório Por Pagamentos.

    PROTEÇÃO (lição 06/07/2026): PULA por completo os boletos DDA de um dia que JÁ tem
    boletos detalhados (origem='dda-detalhe') pra aquela empresa — senão a linha somada
    DUPLICA o que já foi itemizado. Mesma salvaguarda do importar_safra_incremental.py.
    """
    dda_por_dia: dict = defaultdict(lambda: [0.0, None])  # dia -> [soma, data_obj]
    resto: list[dict] = []
    _ja_detalhado: dict = {}

    def dia_detalhado(dia_iso: str) -> bool:
        if dia_iso not in _ja_detalhado:
            _ja_detalhado[dia_iso] = bool(query_one(
                "SELECT 1 FROM lancamentos WHERE empresa_id=? AND data=? "
                "AND origem='dda-detalhe'", (emp_id, dia_iso)))
        return _ja_detalhado[dia_iso]

    for m in movimentos:
        if _eh_dda(m) and m["tipo"] == "saida":
            k = m["data"].isoformat()
            if dia_detalhado(k):
                continue  # dia já itemizado — não recontar
            dda_por_dia[k][0] += m["valor"]
            dda_por_dia[k][1] = m["data"]
        else:
            resto.append(m)
    somados = []
    for _, (soma, data_obj) in dda_por_dia.items():
        d = {
            "data": data_obj,
            "valor": round(soma, 2),
            "tipo": "saida",
            "historico": "Pagamento De Boleto Dda",
            "contraparte": None,
            "cnpj_contraparte": None,
            "documento": None,
            "saldo_apos": None,
        }
        d["linha_hash"] = _hash_mov(d)
        somados.append(d)
    return resto + somados


def main(caminho: str, apelido: str, conta_desc: str, commit: bool) -> None:
    arquivo = Path(caminho)
    file_bytes = arquivo.read_bytes()

    emp = query_one("SELECT id, apelido FROM empresas WHERE apelido = ?", (apelido,))
    if not emp:
        sys.exit(f"Empresa '{apelido}' não encontrada.")
    emp_id = emp["id"]

    conta = query_one(
        "SELECT id FROM contas_bancarias WHERE empresa_id=? AND banco=? AND descricao=?",
        (emp_id, BANCO, conta_desc),
    )
    if not conta:
        sys.exit(f"Conta '{conta_desc}' ({BANCO}) não encontrada em {apelido}. "
                 "Cadastre antes (este import não cria conta).")
    conta_id = conta["id"]

    brutos = parse_extrato(file_bytes, arquivo.name)
    movimentos = _consolidar_dda(brutos, emp_id)

    # Emenda de saldo: pega movimento que o banco lançou depois de o arquivo ser
    # gerado. Roda sobre o parse CRU (`brutos`), antes da consolidação do DDA.
    saldos = ler_saldos(file_bytes, arquivo.name)
    alertas = conferir(conta_id, saldos, brutos)

    regras = regras_ativas()
    planos = {p["id"]: p["nome"] for p in query("SELECT id, nome FROM plano_contas")}
    cat_transf = id_categoria_transferencia_interna()
    cat_aplic = query_one(
        "SELECT id FROM plano_contas WHERE tipo='transferencia' AND nome LIKE '%plica%'")
    cat_aplic_id = cat_aplic["id"] if cat_aplic else None

    for m in movimentos:
        h = _norm(m["historico"])
        interna = empresa_do_grupo_por_cnpj(m.get("cnpj_contraparte"))
        if interna and interna["id"] != emp_id and cat_transf:
            m["_plano_id"], m["_regra"], m["_classif"] = cat_transf, None, True
        elif cat_aplic_id and any(t in h for t in MECANICOS_APLICACAO):
            m["_plano_id"], m["_regra"], m["_classif"] = cat_aplic_id, None, True
        else:
            regra = classificar_movimento(m["historico"], m["tipo"], emp_id, regras)
            m["_regra"] = regra
            m["_plano_id"] = regra["plano_conta_id"] if regra else None
            m["_classif"] = bool(regra)

    existentes = [dict(r) for r in query(
        "SELECT data, valor, tipo, documento, descricao, linha_hash "
        "FROM lancamentos WHERE conta_bancaria_id=?", (conta_id,))]
    hashes_globais = {r["linha_hash"] for r in query(
        "SELECT linha_hash FROM lancamentos WHERE linha_hash IS NOT NULL")}
    a_inserir, duplicados = planejar_insercao(movimentos, existentes, hashes_globais)

    ent = [m for m, _ in a_inserir if m["tipo"] == "entrada"]
    sai = [m for m, _ in a_inserir if m["tipo"] == "saida"]
    print(f"\n=== {arquivo.name} -> {apelido} / {conta_desc} (conta {conta_id}) ===")
    print(f"Parseou {len(movimentos)} (pós-consolidação DDA) · "
          f"NOVOS {len(a_inserir)} · duplicados {duplicados}")
    print(f"  Entradas novas: {len(ent)}  R$ {sum(m['valor'] for m in ent):,.2f}")
    print(f"  Saídas novas:   {len(sai)}  R$ {sum(m['valor'] for m in sai):,.2f}")
    print("\n  -- saídas novas --")
    for m in sorted(sai, key=lambda x: (-x["valor"])):
        cat = planos.get(m["_plano_id"]) or "— PENDENTE —"
        cp = m.get("contraparte") or m["historico"]
        print(f"    {m['data']}  R$ {m['valor']:>11,.2f}  {cp[:34]:34} -> {cat}")

    if saldos:
        print(f"\n  Saldo do extrato ({saldos['periodo_ini']} a {saldos['periodo_fim']}): "
              f"inicial {saldos['saldo_inicial']} · total {saldos['saldo_total']}")
    for a in alertas:
        print(f"\n  ⚠️  {a}")
    if saldos and not alertas:
        if saldos["saldo_inicial"] is None:
            print("  ℹ️  Este extrato não traz SALDO INICIAL — não deu pra conferir a "
                  "emenda com o anterior (só o SALDO TOTAL foi guardado).")
        else:
            print("  ✅ Emenda de saldo confere com o extrato anterior.")

    if not commit:
        print("\n(PREVIEW — rode com --commit pra gravar)")
        return

    bkp = Path(DB_PATH).with_name(f"elaine.backup-{datetime.now():%Y%m%d-%H%M%S}.db")
    shutil.copy2(DB_PATH, bkp)
    print(f"\nBackup: {bkp.name}")

    imp_id = execute(
        "INSERT INTO importacoes (empresa_id, conta_bancaria_id, arquivo_nome, "
        "arquivo_hash, formato, linhas_total) VALUES (?, ?, ?, ?, ?, ?)",
        (emp_id, conta_id, arquivo.name, hash_arquivo(file_bytes), "xls", len(movimentos)))

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
             linha_hash, m["saldo_apos"]))
        if m["_regra"]:
            execute("UPDATE regras_classificacao SET vezes_aplicada=vezes_aplicada+1 "
                    "WHERE id=?", (m["_regra"]["id"],))
        importados += 1

    execute("UPDATE importacoes SET linhas_importadas=?, linhas_duplicadas=? WHERE id=?",
            (importados, duplicados, imp_id))
    if registrar(conta_id, saldos):
        print("Saldo deste extrato guardado (serve de âncora pra emenda do próximo).")
    print(f"Importação {imp_id}: {importados} inseridos · {duplicados} duplicados.")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--commit"]
    commit = "--commit" in sys.argv
    if len(args) < 3:
        sys.exit('Uso: python importar_safra_xls.py <xls> "<Empresa>" "<Conta>" [--commit]')
    main(args[0], args[1], args[2], commit)
