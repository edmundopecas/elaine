"""Importa o EXTRATO XLSX do Santander (colunas Data | Histórico | Documento |
Valor | Saldo) para uma conta já cadastrada, gravando TUDO como PENDENTE
(sem classificar) — o Filipe classifica depois nas telas do app.

Por que XLSX e não OFX: o export OFX do Santander TRUNCA em 400 linhas (perde a
1ª quinzena) e OMITE as aplicações/resgates Contamax. O XLSX traz o mês inteiro
e o SALDO ANTERIOR. O dedup (data,valor,tipo) garante que só entram as linhas que
faltam — re-rodar não duplica.

Uso (preview):  python importar_santander_xls.py
Uso (commit):   python importar_santander_xls.py --commit

Conexão: usa ELAINE_DATABASE_URL (Supabase) se setada, senão SQLite local.
"""
from __future__ import annotations

import re
import sys
from datetime import date, datetime

import pandas as pd

from db import execute, query, query_one
from dedup import planejar_insercao
from parsers import _hash_mov, _RE_CNPJ, _RE_CPF, _so_digitos

# conta_bancaria_id -> arquivo XLSX no Downloads
ARQUIVOS = {
    6: r"C:\Users\Elaine\Downloads\Extrato_23062026095155.xlsx",  # Santander 0004460
    7: r"C:\Users\Elaine\Downloads\Extrato_23062026095354.xlsx",  # Santander 0006668
}

# prefixos de "tipo" do Santander (o que vem depois é a contraparte/identificador)
_PREFIXOS = [
    "RESGATE CONTAMAX AUTOMATICO", "APLICACAO CONTAMAX", "DEBITO EMPRESTIMO",
    "RENDIMENTO LIQUIDO DE CONTAMAX", "TARIFA PIX RECEBIDO QR CHECKOUT",
    "TARIFA AVULSA ENVIO PIX", "PRESTACAO CONSORCIO",
    "PIX RECEBIDO", "PIX ENVIADO",
]


def _parse_valor(v):
    if pd.isna(v):
        return None
    s = str(v).strip().replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _parse_data(v) -> date | None:
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", str(v).strip())
    if not m:
        return None
    return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))


def _eh_saldo(h) -> bool:
    return str(h).strip().lower().startswith("saldo")


def _contraparte(hist_upper: str) -> str | None:
    for pre in _PREFIXOS:
        if hist_upper.startswith(pre):
            resto = hist_upper[len(pre):].strip()
            return resto or None
    return hist_upper or None


def parse_santander_xls(caminho: str) -> list[dict]:
    x = pd.read_excel(caminho, header=None, skiprows=1)
    movs = []
    for _, row in x.iterrows():
        data = _parse_data(row[0])
        valor = _parse_valor(row[3])
        if data is None or valor is None or _eh_saldo(row[1]):
            continue
        hist_up = re.sub(r"\s+", " ", str(row[1]).strip()).upper()
        doc = re.sub(r"\s+", "", str(row[2]).strip())
        if doc in ("", "0", "000000", "nan", "NAN"):
            doc = None
        cp = _contraparte(hist_up)
        cnpj_m = _RE_CNPJ.search(hist_up) or _RE_CPF.search(hist_up)
        d = {
            "data": data,
            "valor": round(abs(valor), 2),
            "tipo": "saida" if valor < 0 else "entrada",
            "historico": hist_up.title(),          # uniforme c/ as linhas já existentes
            "contraparte": (cp.title() if cp and not cp.isdigit() else cp),
            "cnpj_contraparte": _so_digitos(cnpj_m.group(1)) if cnpj_m else None,
            "documento": doc,
            "saldo_apos": None,
        }
        d["linha_hash"] = _hash_mov(d)
        movs.append(d)
    return movs


def main(commit: bool) -> None:
    print("MODO:", "COMMIT (gravando)" if commit else "PREVIEW (dry-run)")
    total_inserir = 0
    for conta_id, caminho in ARQUIVOS.items():
        conta = query_one(
            "SELECT id, empresa_id, banco, descricao FROM contas_bancarias WHERE id=?",
            (conta_id,))
        if not conta:
            print(f"!! conta {conta_id} não existe — pulando")
            continue
        movs = parse_santander_xls(caminho)
        existentes = [dict(r) for r in query(
            "SELECT data, valor, tipo, documento, descricao, linha_hash "
            "FROM lancamentos WHERE conta_bancaria_id=?", (conta_id,))]
        a_inserir, duplicados = planejar_insercao(movs, existentes)
        ent = sum(1 for m, _ in a_inserir if m["tipo"] == "entrada")
        sai = sum(1 for m, _ in a_inserir if m["tipo"] == "saida")
        print(f"\n=== conta {conta_id} ({conta['banco']} {conta['descricao']}) ===")
        print(f"  planilha {len(movs)} | banco {len(existentes)} | "
              f"NOVOS {len(a_inserir)} ({ent} ent / {sai} sai) | duplicados {duplicados}")
        total_inserir += len(a_inserir)
        if not commit or not a_inserir:
            continue

        imp_id = execute(
            "INSERT INTO importacoes (empresa_id, conta_bancaria_id, arquivo_nome, "
            "arquivo_hash, formato, linhas_total) VALUES (?, ?, ?, ?, ?, ?)",
            (conta["empresa_id"], conta_id, caminho.split("\\")[-1],
             _hash_mov({"historico": caminho}), "xlsx", len(movs)))
        n = 0
        for m, lh in a_inserir:
            execute(
                "INSERT INTO lancamentos (empresa_id, conta_bancaria_id, data, descricao, "
                "contraparte, cnpj_contraparte, documento, valor, tipo, plano_conta_id, "
                "centro_custo_id, classificado, origem, regra_id, importacao_id, "
                "linha_hash, saldo_apos) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (conta["empresa_id"], conta_id, m["data"].isoformat(), m["historico"],
                 m["contraparte"], m["cnpj_contraparte"], m["documento"],
                 m["valor"], m["tipo"], None, None, 0, "extrato-xls", None, imp_id,
                 lh, None))
            n += 1
        execute("UPDATE importacoes SET linhas_importadas=?, linhas_duplicadas=? WHERE id=?",
                (n, duplicados, imp_id))
        print(f"  -> importacao_id={imp_id} · {n} lançamentos PENDENTES inseridos")

    print(f"\nTOTAL a inserir: {total_inserir}")
    if not commit:
        print("Foi só PREVIEW. Rode com --commit pra gravar.")


if __name__ == "__main__":
    main(commit="--commit" in sys.argv)
