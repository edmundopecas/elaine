"""
Itemiza a conta Itau (Matriz, conta 11) a partir do relatorio
'consulta-comprovantes.xlsx' (Relatorio de comprovantes bancarios do Itau).

Hoje o Itau esta quase todo lumped como 'SISPAG FORNECEDORES'. Este script troca
esses lumps pelos pagamentos itemizados do relatorio (favorecido, valor, info).

Mantem intactos: as tarifas (TAR...) e os 'SISPAG TRIBUTOS' (impostos, que serao
detalhados por estado noutro momento). Apaga tambem o 'PIX DEVOLVIDO AKM' pois o
relatorio ja traz essa devolucao como 'Pix - devolucao' (evita duplicar).

Classificacao de cada item:
  - info com DEV/DEVOL  ou  tipo 'devolucao'  -> Devolucoes e Descontos
  - senao casa pela Base de Tipos (regras) pelo favorecido
  - senao fica PENDENTE (a classificar) -> Filipe revisa na tela de Saidas

Preview por padrao; grava so com --commit.
"""
from __future__ import annotations

import hashlib
import re
import sys
import unicodedata

import pandas as pd

from classificador import classificar_movimento, regras_ativas
from db import execute, query, query_one

ARQ_PADRAO = r"C:\Users\Elaine\Downloads\consulta-comprovantes.xlsx"
CONTA_ID = 11          # Itau C/C 8293214881
EMPRESA_ID = 1         # Edmundo Matriz
CNPJ_ESPERADO = "06012511000100"
DEVOLUCOES_ID = 6      # Devolucoes e Descontos

# tipos de comprovante que sao IMPOSTO (= SISPAG TRIBUTOS) -> nao itemizar aqui
TIPOS_IMPOSTO = ("codigo de barras", "tributos", "prefeitura")


def _norm(s) -> str:
    s = "" if s is None else str(s)
    s = unicodedata.normalize("NFKD", s).encode("ASCII", "ignore").decode()
    return re.sub(r"\s+", " ", s).strip().lower()


def _digitos(s) -> str:
    return re.sub(r"\D", "", str(s or ""))


_RE_DEV = re.compile(r"\bdev", re.IGNORECASE)


def _eh_devolucao(tipo: str, info: str) -> bool:
    return "devolu" in _norm(tipo) or bool(_RE_DEV.search(str(info or "")))


def _eh_carcaca(info: str) -> bool:
    # carcaça (casco de bateria) na info -> custo de troca de bateria.
    # detecta pela info porque o casco e comprado de pessoas diferentes.
    return "carca" in _norm(info)


def _eh_13o(info: str) -> bool:
    # adiantamento de 13o salario. info vem "13o 1a parcela 2026" (o ordinal
    # vira espaco no _norm). exige comecar com 13 + parcela p/ nao pegar
    # "PARCELA 133" (consorcio) nem "NF 1371.." nem "CARCACA 0013..".
    n = _norm(info)
    return (n.startswith("13") and "parcela" in n) or "decimo terceiro" in n


def _eh_imposto(tipo: str) -> bool:
    t = _norm(tipo)
    return any(k in t for k in TIPOS_IMPOSTO)


# Palavras na info -> categoria (chave do dict `ids`), checadas em ordem.
# Detecta pela info (nao pelo favorecido) porque cada pagamento e p/ pessoa
# diferente; o que define a natureza e o texto "FERIAS/PENSAO/RESCISAO...".
_INFO_KEYWORDS = [
    ("ferias", "ferias"),
    ("rescisao", "rescisao"),
    ("pensao", "pensao"),
    ("anteci salario", "salarios"),
    ("antecip salario", "salarios"),
    ("adiant salario", "salarios"),
]


def _plano_por_info(tipo: str, info: str, fav: str, regras, ids: dict):
    """Decide o plano_conta de um comprovante. Retorna (pid, via)."""
    if _eh_devolucao(tipo, info):
        return ids.get("devolucoes"), "dev"
    if ids.get("carcacas") and _eh_carcaca(info):
        return ids["carcacas"], "carcaca"
    if ids.get("decimo") and _eh_13o(info):
        return ids["decimo"], "13o"
    n = _norm(info)
    for kw, chave in _INFO_KEYWORDS:
        if kw in n and ids.get(chave):
            return ids[chave], chave
    regra = classificar_movimento(fav, "saida", EMPRESA_ID, regras)
    if regra:
        return regra["plano_conta_id"], "regra"
    return None, "pendente"


def main(path: str, commit: bool) -> None:
    bruto = pd.read_excel(path, header=None)
    topo = " ".join(_digitos(x) for x in bruto.head(12).to_numpy().ravel())
    if CNPJ_ESPERADO not in topo:
        print(f"ERRO: nao achei o CNPJ {CNPJ_ESPERADO} no topo do arquivo. "
              "Esse relatorio e da conta Itau Matriz?")
        return

    df = pd.read_excel(path, header=12)
    df.columns = [str(c) for c in df.columns]
    c_data, c_tipo, c_fav, c_val, c_info, c_aut = list(df.columns)[:6]
    df = df[df[c_data].astype(str).str.match(r"\d\d/\d\d/\d\d\d\d", na=False)].copy()
    df["_valor"] = pd.to_numeric(df[c_val], errors="coerce")
    df = df[df["_valor"].notna() & (df["_valor"] > 0)]
    df["_dia"] = pd.to_datetime(df[c_data], dayfirst=True).dt.strftime("%Y-%m-%d")

    # separa impostos (ficam lumped) dos fornecedores (a itemizar)
    df["_imposto"] = df[c_tipo].apply(_eh_imposto)
    forn = df[~df["_imposto"]].copy()
    imp = df[df["_imposto"]].copy()

    print(f"Relatorio: {len(df)} comprovantes - R$ {df['_valor'].sum():,.2f}")
    print(f"  - impostos (cod. barras/tributos, mantidos lumped): "
          f"{len(imp)} - R$ {imp['_valor'].sum():,.2f}")
    print(f"  - fornecedores/pix/boletos (a itemizar):           "
          f"{len(forn)} - R$ {forn['_valor'].sum():,.2f}\n")

    # o que sera APAGADO do banco (lumps de fornecedor + pix devolvido)
    a_apagar = query(
        "SELECT id, data, descricao, valor FROM lancamentos "
        "WHERE conta_bancaria_id=? AND tipo='saida' AND origem='extrato' "
        "AND UPPER(descricao) NOT LIKE 'TAR%' "
        "AND UPPER(descricao) NOT LIKE '%SISPAG TRIBUTOS%'", (CONTA_ID,))
    soma_apagar = sum(abs(r["valor"]) for r in a_apagar)
    print(f"Lumps a apagar do banco: {len(a_apagar)} linhas - R$ {soma_apagar:,.2f}")
    print(f"Itens a inserir:         {len(forn)} - R$ {forn['_valor'].sum():,.2f}")
    print(f"Diferenca liquida no total do Itau: R$ {forn['_valor'].sum() - soma_apagar:+,.2f}\n")

    # classificacao prevista
    regras = regras_ativas()
    planos = {p["id"]: p["nome"] for p in query("SELECT id, nome FROM plano_contas")}

    def _id(like):
        r = query_one("SELECT id FROM plano_contas WHERE nome LIKE ? LIMIT 1", (like,))
        return r["id"] if r else None

    ids = {"devolucoes": DEVOLUCOES_ID, "carcacas": _id("Carca%"),
           "decimo": _id("13%Sal%"), "ferias": _id("F_rias"),
           "rescisao": _id("Rescis%"), "pensao": _id("Pens%"),
           "salarios": _id("Sal_rios")}

    devolucoes, por_regra, pendentes = [], {}, {}
    cat_count: dict[str, float] = {}
    for _, b in forn.iterrows():
        fav = str(b[c_fav]).strip()
        info = str(b[c_info]).strip()
        v = float(b["_valor"])
        pid, via = _plano_por_info(b[c_tipo], info, fav, regras, ids)
        if via == "dev":
            devolucoes.append((b["_dia"], fav, info, v))
        elif via == "regra":
            por_regra[fav] = por_regra.get(fav, 0) + v
        elif via == "pendente":
            pendentes[fav] = pendentes.get(fav, 0) + v
        nome = planos.get(pid, "PENDENTE (a classificar)")
        cat_count[nome] = cat_count.get(nome, 0) + v

    print(f"== DEVOLUCOES detectadas (DEV/DEVOL ou tipo devolucao): {len(devolucoes)} "
          f"- R$ {sum(d[3] for d in devolucoes):,.2f} ==")
    for dia, fav, info, v in sorted(devolucoes, key=lambda x: -x[3]):
        d = pd.to_datetime(dia).strftime("%d/%m")
        print(f"  {d}  R$ {v:>10,.2f}  {info[:38]:<38} {fav[:28]}")

    print(f"\n== Classificacao prevista por categoria ==")
    for nome, v in sorted(cat_count.items(), key=lambda x: -x[1]):
        print(f"  {nome:<35} R$ {v:>13,.2f}")
    print(f"\n  por regra (Base de Tipos): {sum(por_regra.values()):,.2f} em {len(por_regra)} fornecedores")
    print(f"  PENDENTES a classificar:   {sum(pendentes.values()):,.2f} em {len(pendentes)} contrapartes")

    if not commit:
        print("\n[PREVIEW] Nada gravado. Rode com --commit para aplicar.")
        return

    # backup
    import shutil, sqlite3
    from db import DB_PATH
    bk = str(DB_PATH).replace(".db", "") + ".backup-itau.db"
    try:
        shutil.copy(DB_PATH, bk)
        print(f"\nBackup: {bk}")
    except Exception as e:
        print(f"AVISO backup: {e}")

    for r in a_apagar:
        execute("DELETE FROM lancamentos WHERE id=?", (r["id"],))

    inseridos = 0
    for _, b in forn.iterrows():
        dia = b["_dia"]
        fav = str(b[c_fav]).strip()
        info = str(b[c_info]).strip()
        aut = str(b[c_aut]).strip()
        v = float(b["_valor"])
        pid, _via = _plano_por_info(b[c_tipo], info, fav, regras, ids)
        h = hashlib.sha256(f"itau-comprovante|{aut}|{dia}|{v:.2f}".encode()).hexdigest()
        if query_one("SELECT 1 FROM lancamentos WHERE linha_hash=?", (h,)):
            continue
        desc = f"{str(b[c_tipo]).strip()} - {fav}" if fav and fav != "-" else str(b[c_tipo]).strip()
        execute(
            "INSERT INTO lancamentos (empresa_id, conta_bancaria_id, data, descricao, "
            "contraparte, documento, valor, tipo, plano_conta_id, classificado, origem, "
            "linha_hash, observacao) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (EMPRESA_ID, CONTA_ID, dia, desc, (fav if fav != "-" else None),
             (aut[:20] or None), v, "saida", pid, 1 if pid else 0,
             "itau-comprovante", h,
             f"Comprovante Itau | {info}" if info and info != "-" else "Comprovante Itau"))
        inseridos += 1
    print(f"\n[COMMIT] apagados {len(a_apagar)} lumps; inseridos {inseridos} comprovantes.")


if __name__ == "__main__":
    commit = "--commit" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    main(args[0] if args else ARQ_PADRAO, commit)
