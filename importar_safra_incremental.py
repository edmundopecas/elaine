"""Import INCREMENTAL do Safra — só novidades, sem duplicar o que já entrou.

As contas Safra (Matriz/Filial) já foram importadas (com os Boletos DDA de vários
dias detalhados em boletos individuais). Estes re-exports trazem os mesmos dias +
dias novos. Regras de dedup (além do linha_hash):
  1) pula movimento cujo (data, valor, tipo) já existe naquela conta;
  2) pula a linha SOMADA "PAGAMENTO DE BOLETO DDA" de um dia que já tem boletos
     detalhados (origem 'dda-detalhe') — senão recontaria o que já foi itemizado.
Assim só entram os dias/movimentos realmente novos.

Uso: python importar_safra_incremental.py <ofx1> [<ofx2> ...]
"""
from __future__ import annotations

import re
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

MECANICOS_APLICACAO = ("rende facil", "conta remunerada", "na conta corrente")


def _norm(s: str) -> str:
    return unicodedata.normalize("NFKD", s or "").encode("ASCII", "ignore").decode().lower()


def _acctid(file_bytes: bytes) -> str | None:
    txt = file_bytes.decode("latin-1", "ignore")
    m = re.search(r"<ACCTID>([^<\r\n]*)", txt, re.IGNORECASE)
    return m.group(1).strip() if m else None


def importar_arquivo(caminho: str) -> None:
    arquivo = Path(caminho)
    file_bytes = arquivo.read_bytes()
    acct = _acctid(file_bytes)
    conta = query_one(
        "SELECT id, empresa_id, descricao FROM contas_bancarias WHERE descricao LIKE ?",
        (f"%{acct}%",),
    ) if acct else None
    if not conta:
        print(f"  [pulado] {arquivo.name}: conta {acct} não encontrada no banco.")
        return
    conta_id, emp_id = conta["id"], conta["empresa_id"]

    # dias com Boletos DDA já itemizados (origem 'dda-detalhe') — a linha somada do
    # extrato desses dias não deve voltar (recontaria o que já foi detalhado).
    dias_dda = {r["data"] for r in query(
        "SELECT DISTINCT data FROM lancamentos WHERE conta_bancaria_id=? AND origem='dda-detalhe'",
        (conta_id,))}

    movimentos = parse_extrato(file_bytes, arquivo.name)
    # protege a linha somada de DDA de dia já detalhado ANTES do dedup de conteúdo
    candidatos, dda_protegido = [], 0
    for m in movimentos:
        if "boleto dda" in _norm(m["historico"]) and m["data"].isoformat() in dias_dda:
            dda_protegido += 1
        else:
            candidatos.append(m)

    # dedup por multiset de conteúdo (não colapsa parcelas idênticas — ver dedup.py)
    existentes = [dict(r) for r in query(
        "SELECT data, valor, tipo, documento, descricao, linha_hash "
        "FROM lancamentos WHERE conta_bancaria_id=?", (conta_id,))]
    a_inserir, ja_existe = planejar_insercao(candidatos, existentes)

    regras = regras_ativas()
    cat_transf = id_categoria_transferencia_interna()
    cat_aplic = query_one("SELECT id FROM plano_contas WHERE tipo='transferencia' AND nome LIKE '%plica%'")
    cat_aplic_id = cat_aplic["id"] if cat_aplic else None

    imp_id = execute(
        "INSERT INTO importacoes (empresa_id, conta_bancaria_id, arquivo_nome, "
        "arquivo_hash, formato, linhas_total) VALUES (?, ?, ?, ?, ?, ?)",
        (emp_id, conta_id, arquivo.name, hash_arquivo(file_bytes), "ofx", len(movimentos)),
    )

    novos = 0
    for m, linha_hash in a_inserir:
        # classifica (interna por CNPJ -> mecânico aplicação -> regras)
        h = _norm(m["historico"])
        interna = empresa_do_grupo_por_cnpj(m.get("cnpj_contraparte"))
        # Só transferência se vier de OUTRA empresa do grupo; CNPJ próprio = venda
        # no PIX da loja, deixa as regras decidirem (fix 23/06).
        if interna and interna["id"] != emp_id and cat_transf:
            plano_id, regra, classif = cat_transf, None, True
        elif cat_aplic_id and any(t in h for t in MECANICOS_APLICACAO):
            plano_id, regra, classif = cat_aplic_id, None, True
        else:
            regra = classificar_movimento(m["historico"], m["tipo"], emp_id, regras)
            plano_id, classif = (regra["plano_conta_id"] if regra else None), bool(regra)

        execute(
            "INSERT INTO lancamentos (empresa_id, conta_bancaria_id, data, descricao, "
            "contraparte, cnpj_contraparte, documento, valor, tipo, plano_conta_id, "
            "centro_custo_id, classificado, origem, regra_id, importacao_id, "
            "linha_hash, saldo_apos) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (emp_id, conta_id, m["data"].isoformat(), m["historico"], m.get("contraparte"),
             m.get("cnpj_contraparte"), m["documento"], m["valor"], m["tipo"],
             plano_id, None, 1 if classif else 0, "extrato",
             regra["id"] if regra else None, imp_id, linha_hash, m["saldo_apos"]),
        )
        if regra:
            execute("UPDATE regras_classificacao SET vezes_aplicada=vezes_aplicada+1 WHERE id=?",
                    (regra["id"],))
        novos += 1

    execute("UPDATE importacoes SET linhas_importadas=?, linhas_duplicadas=? WHERE id=?",
            (novos, ja_existe + dda_protegido, imp_id))
    print(f"  {arquivo.name}  ({conta['descricao']}): {novos} novos · "
          f"{ja_existe} já existiam · {dda_protegido} linhas DDA protegidas (dia já detalhado)")


def main(caminhos: list[str]) -> None:
    bkp = Path(DB_PATH).with_name(f"elaine.backup-{datetime.now():%Y%m%d-%H%M%S}.db")
    shutil.copy2(DB_PATH, bkp)
    print(f"Backup: {bkp.name}\n")
    for c in caminhos:
        importar_arquivo(c)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("Uso: python importar_safra_incremental.py <ofx1> [<ofx2> ...]")
    main(sys.argv[1:])
