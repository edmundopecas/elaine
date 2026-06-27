"""Importa o EXTRATO XLSX do Asaas (Supernova) — só o que falta, com dedup.

O Asaas exporta o extrato em XLSX (não OFX). Formato:
  - linha "Período a partir de DD/MM/AAAA até DD/MM/AAAA"
  - cabeçalho: Data | Transação | Tipo de transação | Transação estornada |
               Descrição | Valor | Saldo | ... (Valor com sinal: -=débito/saída)
  - linha "Saldo Inicial | <valor>" (ignorada — não tem Valor na coluna Valor)
  - movimentos: Cobrança recebida (entrada), Taxa de boleto/mensageria/notificação
    (saída mínima), Transação via Pix (saída).

Classifica igual ao importar_ofx.py: (1) transferência interna por CNPJ de OUTRA
empresa do grupo (CNPJ próprio NÃO vira transferência — fix 23/06), (2) regras
de-para (taxa de boleto/mensageria/notificação -> Taxas de Cartão/Adquirente;
Cobrança recebida na Supernova -> Receita de Aluguel via regra RECEBI); o resto
fica pendente. Dedup por multiset (data, valor, tipo) + sufixo de hash global.

Uso:  python importar_asaas_xls.py "<arquivo.xlsx>"            (preview)
      python importar_asaas_xls.py "<arquivo.xlsx>" --commit   (grava)
"""
from __future__ import annotations

import html
import re
import sys
import unicodedata

import pandas as pd

from classificador import (classificar_movimento, empresa_do_grupo_por_cnpj,
                           id_categoria_transferencia_interna, regras_ativas)
from db import execute, query, query_one
from parsers import (_RE_CNPJ, _RE_CPF, _hash_mov, _limpar_contraparte,
                     _parse_data, _so_digitos)
from dedup import planejar_insercao

MECANICOS_APLICACAO = ("rende facil", "conta remunerada", "na conta corrente")


def _norm(s: str) -> str:
    return unicodedata.normalize("NFKD", s or "").encode("ASCII", "ignore").decode().lower()


def parse_asaas_xls(caminho: str) -> list[dict]:
    raw = pd.read_excel(caminho, sheet_name=0, header=None)
    hdr = next(i for i in range(len(raw))
               if "Data" in [str(x).strip() for x in raw.iloc[i].tolist()]
               and "Valor" in [str(x).strip() for x in raw.iloc[i].tolist()])
    df = pd.read_excel(caminho, sheet_name=0, header=hdr).dropna(how="all")

    movs = []
    for _, row in df.iterrows():
        data = _parse_data(row.get("Data"))
        valor = row.get("Valor")
        if data is None or pd.isna(valor) or float(valor) == 0:
            continue
        desc = re.sub(r"\s+", " ", html.unescape(str(row.get("Descrição") or "")).strip())
        if desc.lower().startswith("saldo"):
            continue
        v = float(valor)
        cnpj_m = _RE_CNPJ.search(desc) or _RE_CPF.search(desc)
        doc = row.get("Transação")
        doc = str(int(doc)) if pd.notna(doc) and str(doc) != "" else None
        cp = _limpar_contraparte(desc)
        d = {
            "data": data,
            "valor": round(abs(v), 2),
            "tipo": "saida" if v < 0 else "entrada",
            "historico": desc,
            "contraparte": cp or None,
            "cnpj_contraparte": _so_digitos(cnpj_m.group(1)) if cnpj_m else None,
            "documento": doc,
            "saldo_apos": None,
        }
        d["linha_hash"] = _hash_mov(d)
        movs.append(d)
    return movs


def main(caminho: str, commit: bool) -> None:
    print("MODO:", "COMMIT" if commit else "PREVIEW")
    conta = query_one("SELECT id, empresa_id, descricao FROM contas_bancarias WHERE banco='Asaas'")
    if not conta:
        sys.exit("Conta Asaas não encontrada no cadastro.")
    cid, emp_id = conta["id"], conta["empresa_id"]

    movs = parse_asaas_xls(caminho)
    existentes = [dict(r) for r in query(
        "SELECT data, valor, tipo, documento, descricao, linha_hash "
        "FROM lancamentos WHERE conta_bancaria_id=?", (cid,))]
    hashes_globais = {r["linha_hash"] for r in query(
        "SELECT linha_hash FROM lancamentos WHERE linha_hash IS NOT NULL")}
    a_inserir, dup = planejar_insercao(movs, existentes, hashes_globais)

    regras = regras_ativas()
    planos = {p["id"]: p["nome"] for p in query("SELECT id, nome FROM plano_contas")}
    cat_transf = id_categoria_transferencia_interna()
    cat_aplic = query_one("SELECT id FROM plano_contas WHERE tipo='transferencia' AND nome LIKE '%plica%'")
    cat_aplic_id = cat_aplic["id"] if cat_aplic else None

    ent = sum(1 for m, _ in a_inserir if m["tipo"] == "entrada")
    sai = sum(1 for m, _ in a_inserir if m["tipo"] == "saida")
    print(f"\n=== {conta['descricao']} (conta {cid}) ===")
    print(f"  XLSX {len(movs)} | banco {len(existentes)} | NOVOS {len(a_inserir)} ({ent} ent/{sai} sai) | dup {dup}")

    def classifica(m):
        h = _norm(m["historico"])
        interna = empresa_do_grupo_por_cnpj(m.get("cnpj_contraparte"))
        if interna and interna["id"] != emp_id and cat_transf:
            return cat_transf, None
        if cat_aplic_id and any(t in h for t in MECANICOS_APLICACAO):
            return cat_aplic_id, None
        regra = classificar_movimento(m["historico"], m["tipo"], emp_id, regras)
        return (regra["plano_conta_id"] if regra else None), regra

    for m, _ in sorted(a_inserir, key=lambda x: x[0]["data"]):
        pid, regra = classifica(m)
        cat = planos.get(pid) or "— PENDENTE —"
        print(f"     {m['data']} {m['tipo']:7} R$ {m['valor']:>10,.2f}  {(m['contraparte'] or m['historico'])[:34]:34.34} -> {cat}")

    if not commit or not a_inserir:
        if not commit:
            print("\nPREVIEW — rode com --commit pra gravar.")
        return

    imp_id = execute(
        "INSERT INTO importacoes (empresa_id, conta_bancaria_id, arquivo_nome, "
        "arquivo_hash, formato, linhas_total) VALUES (?, ?, ?, ?, ?, ?)",
        (emp_id, cid, caminho.split("\\")[-1].split("/")[-1],
         _hash_mov({"historico": caminho}), "xlsx", len(movs)))
    n = 0
    for m, lh in a_inserir:
        pid, regra = classifica(m)
        execute(
            "INSERT INTO lancamentos (empresa_id, conta_bancaria_id, data, descricao, "
            "contraparte, cnpj_contraparte, documento, valor, tipo, plano_conta_id, "
            "centro_custo_id, classificado, origem, regra_id, importacao_id, "
            "linha_hash, saldo_apos) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (emp_id, cid, m["data"].isoformat(), m["historico"], m["contraparte"],
             m["cnpj_contraparte"], m["documento"], m["valor"], m["tipo"], pid, None,
             1 if pid else 0, "asaas-xls", regra["id"] if regra else None, imp_id, lh, None))
        if regra:
            execute("UPDATE regras_classificacao SET vezes_aplicada=vezes_aplicada+1 WHERE id=?",
                    (regra["id"],))
        n += 1
    execute("UPDATE importacoes SET linhas_importadas=?, linhas_duplicadas=? WHERE id=?",
            (n, dup, imp_id))
    print(f"\n  -> importacao_id={imp_id} · {n} novos inseridos")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--commit"]
    if not args:
        sys.exit('Uso: python importar_asaas_xls.py "<arquivo.xlsx>" [--commit]')
    main(args[0], commit="--commit" in sys.argv)
