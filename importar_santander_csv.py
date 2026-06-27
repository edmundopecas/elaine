"""Importa EXTRATOS CSV do Santander (formato web: linha 'AGENCIA;..;CONTA;..',
linha em branco, depois 'Data;Histórico;Documento;Valor (R$);Saldo (R$)').
Grava só o que FALTA (dedup por data,valor,tipo) como PENDENTE. A conta é
identificada pelos dígitos do header (mapa CONTA_DIG->conta_bancaria_id).

Uso:  python importar_santander_csv.py            (preview)
      python importar_santander_csv.py --commit   (grava)
"""
from __future__ import annotations

import csv
import glob
import os
import re
import sys

from db import execute, query, query_one
from dedup import planejar_insercao
from parsers import _hash_mov, _RE_CNPJ, _RE_CPF, _so_digitos

PASTA = r"C:\Users\Elaine\Downloads\excel extratos"
MAP = {"130006668": 7, "130004460": 6, "130091938": 8}  # dígitos no header -> conta_id

_PREFIXOS = [
    "RESGATE CONTAMAX AUTOMATICO", "APLICACAO CONTAMAX",
    "RENDIMENTO LIQUIDO DE CONTAMAX", "DEBITO EMPRESTIMO",
    "CR COB BLOQ COMP CONF RECEBIMENTO", "PIX RECEBIDO", "PIX ENVIADO",
    "TARIFA PAGAMENTO BOLETO VIA QRCODE", "TARIFA PIX RECEBIDO QR CHECKOUT",
    "TAR PRORROG VCTO COB SIMP-ELETR", "PRESTACAO CONSORCIO",
]


def _pv(s):
    s = str(s).strip().replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _pdate(s):
    from datetime import date
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", str(s).strip())
    return date(int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else None


def _contraparte(h: str):
    for pre in _PREFIXOS:
        if h.startswith(pre):
            resto = h[len(pre):].strip()
            return resto or None
    return h or None


def parse_csv_santander(caminho: str):
    lines = open(caminho, encoding="latin-1").read().splitlines()
    conta_dig = re.search(r"CONTA;\"?(\d+)\"?", lines[0]).group(1)
    start = next(i for i, l in enumerate(lines)
                 if l.lower().startswith('"data"') or l.lower().startswith('data;'))
    movs = []
    for row in csv.reader(lines[start + 1:], delimiter=";"):
        if len(row) < 4:
            continue
        data = _pdate(row[0]); valor = _pv(row[3]); hist = re.sub(r"\s+", " ", row[1].strip())
        if data is None or valor is None or hist.lower().startswith("saldo"):
            continue
        hist_up = hist.upper()
        doc = re.sub(r"\s+", "", str(row[2]).strip())
        if doc in ("", "0", "000000"):
            doc = None
        cp = _contraparte(hist_up)
        cnpj_m = _RE_CNPJ.search(hist_up) or _RE_CPF.search(hist_up)
        d = {
            "data": data, "valor": round(abs(valor), 2),
            "tipo": "saida" if valor < 0 else "entrada",
            "historico": hist.title(),
            "contraparte": (cp.title() if cp and not cp.isdigit() else cp),
            "cnpj_contraparte": _so_digitos(cnpj_m.group(1)) if cnpj_m else None,
            "documento": doc, "saldo_apos": None,
        }
        d["linha_hash"] = _hash_mov(d)
        movs.append(d)
    return conta_dig, movs


def main(commit: bool):
    print("MODO:", "COMMIT" if commit else "PREVIEW")
    total = 0
    for f in sorted(glob.glob(os.path.join(PASTA, "*.csv"))):
        conta_dig, movs = parse_csv_santander(f)
        cid = MAP.get(conta_dig)
        if not cid:
            print(f"\n!! {os.path.basename(f)}: conta {conta_dig} não mapeada — pulando")
            continue
        conta = query_one("SELECT empresa_id, banco, descricao FROM contas_bancarias WHERE id=?", (cid,))
        existentes = [dict(r) for r in query(
            "SELECT data, valor, tipo, documento, descricao, linha_hash "
            "FROM lancamentos WHERE conta_bancaria_id=?", (cid,))]
        hashes_globais = {r["linha_hash"] for r in query(
            "SELECT linha_hash FROM lancamentos WHERE linha_hash IS NOT NULL")}
        a_inserir, dup = planejar_insercao(movs, existentes, hashes_globais)
        ent = sum(1 for m, _ in a_inserir if m["tipo"] == "entrada")
        sai = sum(1 for m, _ in a_inserir if m["tipo"] == "saida")
        print(f"\n=== {os.path.basename(f)} -> conta {cid} ({conta['descricao']}) ===")
        print(f"  CSV {len(movs)} | banco {len(existentes)} | NOVOS {len(a_inserir)} "
              f"({ent} ent/{sai} sai) | dup {dup}")
        for m, _ in sorted(a_inserir, key=lambda x: -x[0]["valor"])[:8]:
            print(f"     {m['data']} {m['tipo']:7} R$ {m['valor']:>10,.2f}  {(m['contraparte'] or m['historico'])[:36]}")
        total += len(a_inserir)
        if not commit or not a_inserir:
            continue
        imp_id = execute(
            "INSERT INTO importacoes (empresa_id, conta_bancaria_id, arquivo_nome, "
            "arquivo_hash, formato, linhas_total) VALUES (?, ?, ?, ?, ?, ?)",
            (conta["empresa_id"], cid, os.path.basename(f),
             _hash_mov({"historico": f}), "csv", len(movs)))
        n = 0
        for m, lh in a_inserir:
            execute(
                "INSERT INTO lancamentos (empresa_id, conta_bancaria_id, data, descricao, "
                "contraparte, cnpj_contraparte, documento, valor, tipo, plano_conta_id, "
                "centro_custo_id, classificado, origem, regra_id, importacao_id, "
                "linha_hash, saldo_apos) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (conta["empresa_id"], cid, m["data"].isoformat(), m["historico"],
                 m["contraparte"], m["cnpj_contraparte"], m["documento"],
                 m["valor"], m["tipo"], None, None, 0, "extrato-csv", None, imp_id,
                 lh, None))
            n += 1
        execute("UPDATE importacoes SET linhas_importadas=?, linhas_duplicadas=? WHERE id=?",
                (n, dup, imp_id))
        print(f"  -> importacao_id={imp_id} · {n} pendentes inseridos")
    print(f"\nTOTAL a inserir: {total}")
    if not commit:
        print("PREVIEW — rode com --commit pra gravar.")


if __name__ == "__main__":
    main(commit="--commit" in sys.argv)
