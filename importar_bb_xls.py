"""Importa o EXTRATO XLSX do Banco do Brasil (export web "Extrato conta corrente")
para uma conta do grupo, rodando o MESMO motor de classificação do importar_ofx.py.

Formato do XLSX (header na linha 1):
    Data | Lançamento | Detalhes | N° documento | Valor | Tipo Lançamento
  - Valor vem como "1.234,56 C" (crédito=entrada) ou "-3.608,85 D" (débito=saída);
    o SINAL/sufixo C/D manda (não confiar na coluna texto, que pode vir com acento).
  - Detalhes traz "DD/MM HH:MM CNPJ NOME" nos PIX (pagador + CNPJ).
  - Linhas "Saldo Anterior" / "Saldo do dia" / "S A L D O" são ignoradas.

Classifica igual ao importar_ofx: (1) transferência interna só quando o CNPJ é de
OUTRA empresa do grupo; (2) "BB Rende Fácil" = movimento mecânico da conta
rendimento -> Aplicação/Resgate (fora da DRE); (3) regras de-para (CIELO->Vendas,
TARIFA->Outras Desp Financeiras etc.); o resto fica PENDENTE pro Filipe classificar.

Uso (preview):  python importar_bb_xls.py <xlsx> "<Empresa>" "<Descrição da conta>"
Uso (commit):   python importar_bb_xls.py <xlsx> "<Empresa>" "<Descrição da conta>" --commit
  (banco = "Banco do Brasil" por padrão)

Conexão: usa ELAINE_DATABASE_URL / secrets.toml (Supabase) se houver, senão SQLite.
"""
from __future__ import annotations

import re
import sys
import unicodedata
from datetime import date
from pathlib import Path

import pandas as pd

from classificador import (classificar_movimento, empresa_do_grupo_por_cnpj,
                           id_categoria_transferencia_interna, regras_ativas)
from db import execute, query, query_one
from dedup import planejar_insercao
from parsers import (_hash_mov, _limpar_contraparte, _RE_CNPJ, _RE_CPF,
                     _so_digitos)

# Históricos que são só dinheiro indo/voltando da conta rendimento (não é gasto):
MECANICOS_APLICACAO = ("rende facil",)


def _norm(s: str) -> str:
    return unicodedata.normalize("NFKD", s or "").encode("ASCII", "ignore").decode().lower()


def _parse_valor(v):
    """'1.234,56 C' -> +1234.56 ; '-3.608,85 D' -> -3608.85"""
    if pd.isna(v):
        return None
    s = str(v).strip()
    neg = s.endswith("D") or s.startswith("-")
    s = re.sub(r"[CD]\s*$", "", s).replace("R$", "").strip()
    s = s.replace(".", "").replace(",", ".")
    try:
        val = abs(float(s))
    except ValueError:
        return None
    return -val if neg else val


def _parse_data(v) -> date | None:
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", str(v).strip())
    if not m:
        return None
    return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))


def _eh_saldo(lanc) -> bool:
    s = re.sub(r"\s+", " ", str(lanc).strip().lower())
    return s.startswith("saldo") or s == "s a l d o"


def parse_bb_xls(caminho: str) -> list[dict]:
    x = pd.read_excel(caminho, header=None, skiprows=1)
    movs = []
    for _, row in x.iterrows():
        data = _parse_data(row[0])
        valor = _parse_valor(row[4])
        if data is None or valor is None or _eh_saldo(row[1]):
            continue
        lanc = re.sub(r"\s+", " ", str(row[1]).strip())
        detalhes = "" if pd.isna(row[2]) else re.sub(r"\s+", " ", str(row[2]).strip())
        doc = re.sub(r"\s+", "", str(row[3]).strip())
        if doc.lower() in ("", "0", "nan"):
            doc = None

        cnpj_m = _RE_CNPJ.search(detalhes) or _RE_CPF.search(detalhes)
        cp = _limpar_contraparte(detalhes) if detalhes else ""
        # histórico = tipo do lançamento + nome da contraparte (pra as regras casarem:
        # "...Cielo..." -> Vendas, "BB Rende Fácil" -> Aplicação, "Tarifa..." -> Desp Fin)
        historico = f"{lanc} {cp}".strip() if cp else lanc
        d = {
            "data": data,
            "valor": round(abs(valor), 2),
            "tipo": "saida" if valor < 0 else "entrada",
            "historico": historico.title(),
            "contraparte": (cp.title() if cp and not cp.isdigit() else (cp or None)),
            "cnpj_contraparte": _so_digitos(cnpj_m.group(1)) if cnpj_m else None,
            "documento": doc,
            "saldo_apos": None,
        }
        d["linha_hash"] = _hash_mov(d)
        movs.append(d)
    return movs


def main(caminho: str, apelido: str, conta_desc: str, banco: str, commit: bool) -> None:
    print("MODO:", "COMMIT (gravando)" if commit else "PREVIEW (dry-run)")
    arquivo = Path(caminho)

    emp = query_one("SELECT id, apelido FROM empresas WHERE apelido = ?", (apelido,))
    if not emp:
        sys.exit(f"Empresa '{apelido}' não encontrada.")
    emp_id = emp["id"]

    conta = query_one(
        "SELECT id FROM contas_bancarias WHERE empresa_id=? AND banco=? AND descricao=?",
        (emp_id, banco, conta_desc))
    if conta:
        conta_id = conta["id"]
        print(f"Conta existente: {banco} - {conta_desc} (id {conta_id})")
    elif commit:
        conta_id = execute(
            "INSERT INTO contas_bancarias (empresa_id, banco, descricao, ativa) "
            "VALUES (?, ?, ?, 1)", (emp_id, banco, conta_desc))
        print(f"Conta CADASTRADA: {banco} - {conta_desc} (id {conta_id}) -> {emp['apelido']}")
    else:
        conta_id = None
        print(f"Conta NOVA a cadastrar: {banco} - {conta_desc} -> {emp['apelido']} (só no commit)")

    movs = parse_bb_xls(caminho)

    # classificação (mesma lógica do importar_ofx.py)
    regras = regras_ativas()
    planos = {p["id"]: p["nome"] for p in query("SELECT id, nome FROM plano_contas")}
    cat_transf = id_categoria_transferencia_interna()
    cat_aplic = query_one(
        "SELECT id FROM plano_contas WHERE tipo='transferencia' AND nome LIKE '%plica%'")
    cat_aplic_id = cat_aplic["id"] if cat_aplic else None

    for m in movs:
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

    # dedup por multiset de conteúdo + sufixo global (ver dedup.py)
    existentes = [dict(r) for r in query(
        "SELECT data, valor, tipo, documento, descricao, linha_hash "
        "FROM lancamentos WHERE conta_bancaria_id=?", (conta_id,))] if conta_id else []
    hashes_globais = {r["linha_hash"] for r in query(
        "SELECT linha_hash FROM lancamentos WHERE linha_hash IS NOT NULL")}
    a_inserir, duplicados = planejar_insercao(movs, existentes, hashes_globais)

    ent = [m for m, _ in a_inserir if m["tipo"] == "entrada"]
    sai = [m for m, _ in a_inserir if m["tipo"] == "saida"]
    pend = sum(1 for m, _ in a_inserir if not m["_classif"])
    print(f"\nplanilha {len(movs)} | banco(conta) {len(existentes)} | "
          f"NOVOS {len(a_inserir)} ({len(ent)} ent / {len(sai)} sai) | "
          f"duplicados {duplicados} | PENDENTES {pend}")

    def _dump(titulo, itens):
        print(f"\n=== {titulo} ({len(itens)}) ===")
        for m in sorted(itens, key=lambda x: (-x["valor"])):
            cat = planos.get(m["_plano_id"]) or "— PENDENTE —"
            cp = (m.get("contraparte") or "(sem contraparte)")
            print(f"  {m['data']}  R$ {m['valor']:>10,.2f}  {cp:28.28}  -> {cat}")
    _dump("ENTRADAS", ent)
    _dump("SAÍDAS", sai)

    if not commit:
        print("\nFoi só PREVIEW. Rode com --commit pra gravar.")
        return
    if not a_inserir:
        print("\nNada novo a inserir.")
        return

    imp_id = execute(
        "INSERT INTO importacoes (empresa_id, conta_bancaria_id, arquivo_nome, "
        "arquivo_hash, formato, linhas_total) VALUES (?, ?, ?, ?, ?, ?)",
        (emp_id, conta_id, arquivo.name, _hash_mov({"historico": arquivo.name}),
         "xlsx", len(movs)))
    n = 0
    for m, lh in a_inserir:
        execute(
            "INSERT INTO lancamentos (empresa_id, conta_bancaria_id, data, descricao, "
            "contraparte, cnpj_contraparte, documento, valor, tipo, plano_conta_id, "
            "centro_custo_id, classificado, origem, regra_id, importacao_id, "
            "linha_hash, saldo_apos) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (emp_id, conta_id, m["data"].isoformat(), m["historico"],
             m["contraparte"], m["cnpj_contraparte"], m["documento"], m["valor"],
             m["tipo"], m["_plano_id"], None, 1 if m["_classif"] else 0,
             "extrato-xls", m["_regra"]["id"] if m["_regra"] else None, imp_id,
             lh, None))
        if m["_regra"]:
            execute("UPDATE regras_classificacao SET vezes_aplicada=vezes_aplicada+1 "
                    "WHERE id=?", (m["_regra"]["id"],))
        n += 1
    execute("UPDATE importacoes SET linhas_importadas=?, linhas_duplicadas=? WHERE id=?",
            (n, duplicados, imp_id))
    print(f"\n-> importacao_id={imp_id} · {n} inseridos ({pend} pendentes). "
          f"Reverter: DELETE FROM lancamentos WHERE importacao_id={imp_id}")


if __name__ == "__main__":
    argv = [a for a in sys.argv[1:] if a != "--commit"]
    commit = "--commit" in sys.argv
    if len(argv) < 3:
        sys.exit('Uso: python importar_bb_xls.py <xlsx> "<Empresa>" "<Descrição da conta>" '
                 '["<Banco>"] [--commit]')
    caminho, apelido, conta_desc = argv[0], argv[1], argv[2]
    banco = argv[3] if len(argv) > 3 else "Banco do Brasil"
    main(caminho, apelido, conta_desc, banco, commit)
