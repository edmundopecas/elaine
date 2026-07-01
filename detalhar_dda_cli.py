"""
CLI que replica a tela 'Detalhar Boletos DDA' (views/boletos_dda.py) pra rodar
fora do Streamlit. Preview por padrão; grava só com --commit.
Mesma lógica: confronta dia a dia com o extrato, troca a linha somada pelos
boletos itemizados (classificados pela Base de Tipos; sobra = Mercadoria).
"""
from __future__ import annotations

import hashlib
import re
import sys
import unicodedata

import pandas as pd

from classificador import classificar_movimento, regras_ativas
from db import execute, query, query_one


def _norm(s) -> str:
    s = "" if s is None else str(s)
    s = unicodedata.normalize("NFKD", s).encode("ASCII", "ignore").decode()
    return re.sub(r"\s+", " ", s).strip().lower()


def _digitos(s) -> str:
    return re.sub(r"\D", "", str(s or ""))


def _achar_cabecalho(bruto: pd.DataFrame):
    for i in range(min(30, len(bruto))):
        celulas = [_norm(x) for x in bruto.iloc[i].tolist()]
        if any(c == "data" for c in celulas) and any(
            "favorecido" in c or "beneficiario" in c for c in celulas
        ):
            return i
    return None


def _coluna(cols, *chaves):
    for c in cols:
        n = _norm(c)
        if any(k in n for k in chaves):
            return c
    return None


def main(path: str, commit: bool, sem_fallback: bool = False) -> None:
    CMV = query_one("SELECT id FROM plano_contas WHERE nome LIKE '%Mercadoria%' "
                    "AND tipo='despesa' ORDER BY ordem LIMIT 1")
    CMV_ID = CMV["id"] if CMV else None
    # Com --sem-fallback: boleto sem regra na Base de Tipos NÃO vira Mercadoria;
    # entra PENDENTE (plano nulo, classificado=0) pro Filipe classificar na mão.
    FALLBACK_ID = None if sem_fallback else CMV_ID

    bruto = pd.read_excel(path, header=None)
    hi = _achar_cabecalho(bruto)
    if hi is None:
        print("ERRO: não reconheci o cabeçalho (Data/Favorecido).")
        return

    df = pd.read_excel(path, header=hi)
    cols = list(df.columns)
    c_data = _coluna(cols, "data")
    c_fav = _coluna(cols, "favorecido", "beneficiario")
    c_cnpj = _coluna(cols, "cnpj", "cpf")
    c_val = _coluna(cols, "valor")
    c_mod = _coluna(cols, "modalidade")
    c_sit = _coluna(cols, "situacao")
    c_doc = _coluna(cols, "documento")

    df = df[df[c_data].astype(str).str.match(r"\d\d/\d\d/\d\d\d\d", na=False)].copy()
    df["_valor"] = pd.to_numeric(df[c_val], errors="coerce")
    if c_mod:
        df = df[df[c_mod].astype(str).str.contains("DDA", case=False, na=False)]
    if c_sit:
        df = df[df[c_sit].astype(str).str.upper().str.strip() == "PAGO"]
    df = df[df["_valor"].notna() & (df["_valor"] > 0)]
    if df.empty:
        print("Nenhum boleto DDA pago encontrado.")
        return
    df["_dia"] = pd.to_datetime(df[c_data], dayfirst=True).dt.strftime("%Y-%m-%d")

    # detecta empresa pelos dígitos da conta no topo
    texto_topo = " ".join(_digitos(x) for x in bruto.head(hi).to_numpy().ravel())
    emp_auto = None
    for r in query("SELECT empresa_id, descricao FROM contas_bancarias"):
        conta_dig = _digitos(r["descricao"])
        if conta_dig and conta_dig[-7:] in texto_topo:
            emp_auto = r["empresa_id"]
            break
    if emp_auto is None:
        print("ERRO: não detectei a empresa/conta pelo topo do arquivo.")
        return
    emp = query_one("SELECT id, apelido FROM empresas WHERE id=?", (emp_auto,))
    emp_id = emp["id"]
    conta = query_one("SELECT id FROM contas_bancarias WHERE empresa_id=?", (emp_id,))
    conta_id = conta["id"] if conta else None
    print(f"Empresa detectada: {emp['apelido']} (id={emp_id})")
    print(f"{len(df)} boletos DDA pagos · total R$ {df['_valor'].sum():,.2f}\n")

    regras = regras_ativas()
    linhas = []
    for dia, g in df.groupby("_dia"):
        plan = g["_valor"].sum()
        ja = query_one("SELECT COUNT(*) n FROM lancamentos WHERE empresa_id=? AND data=? "
                       "AND origem='dda-detalhe'", (emp_id, dia))["n"]
        somado = query_one(
            "SELECT COALESCE(SUM(valor),0) v FROM lancamentos WHERE empresa_id=? AND data=? "
            "AND origem='extrato' AND UPPER(descricao) LIKE '%BOLETO DDA%'", (emp_id, dia))["v"]
        if ja:
            status = "já detalhado"
        elif somado == 0:
            status = "sem linha no extrato"
        elif abs(somado - plan) < 0.01:
            status = "bate certinho"
        else:
            status = f"difere {plan - somado:+,.2f}"
        linhas.append({"dia": dia, "boletos": len(g), "plan": plan,
                       "somado": somado, "status": status})

    print(f"{'Dia':<12}{'Bol':>4}{'Total planilha':>18}{'Extrato somado':>18}   Status")
    for r in linhas:
        d = pd.to_datetime(r["dia"]).strftime("%d/%m/%Y")
        print(f"{d:<12}{r['boletos']:>4}{r['plan']:>18,.2f}{r['somado']:>18,.2f}   {r['status']}")

    a_processar = [r for r in linhas if r["status"] == "bate certinho"
                   or r["status"].startswith("difere")]
    ja_feitos = [r for r in linhas if r["status"] == "já detalhado"]
    sem_extrato = [r for r in linhas if r["status"] == "sem linha no extrato"]
    print(f"\nA processar: {len(a_processar)} dia(s) · "
          f"já feitos: {len(ja_feitos)} · sem linha no extrato: {len(sem_extrato)}")

    # prévia de classificação dos boletos a processar
    dias_proc = {r["dia"] for r in a_processar}
    prev = df[df["_dia"].isin(dias_proc)].copy()
    n_regra = n_cmv = 0
    cat_count: dict[str, float] = {}
    planos = {p["id"]: p["nome"] for p in query("SELECT id, nome FROM plano_contas")}
    for _, b in prev.iterrows():
        fav = str(b[c_fav]).strip()
        regra = classificar_movimento(fav, "saida", emp_id, regras)
        pid = regra["plano_conta_id"] if regra else FALLBACK_ID
        if regra:
            n_regra += 1
        else:
            n_cmv += 1
        nome = planos.get(pid, "— PENDENTE (sem regra) —")
        cat_count[nome] = cat_count.get(nome, 0) + float(b["_valor"])
    destino = "→ PENDENTE (você classifica)" if sem_fallback else "→ Mercadoria"
    print(f"\nClassificação prevista dos {len(prev)} boletos a processar:")
    print(f"  · por regra (Base de Tipos): {n_regra}")
    print(f"  · sem regra {destino}: {n_cmv}")
    print("  Por categoria (valor):")
    for nome, v in sorted(cat_count.items(), key=lambda x: -x[1]):
        print(f"    - {nome:<35}R$ {v:>14,.2f}")

    if not commit:
        print("\n[PREVIEW] Nada foi gravado. Rode com --commit para aplicar.")
        return

    # ── grava (mesma lógica da tela) ──
    total_inseridos = 0
    for r in a_processar:
        dia = r["dia"]
        execute("DELETE FROM lancamentos WHERE empresa_id=? AND data=? AND origem='extrato' "
                "AND UPPER(descricao) LIKE '%BOLETO DDA%'", (emp_id, dia))
        for _, b in df[df["_dia"] == dia].iterrows():
            fav = str(b[c_fav]).strip()
            regra = classificar_movimento(fav, "saida", emp_id, regras)
            pid = regra["plano_conta_id"] if regra else FALLBACK_ID
            classif = 1 if pid is not None else 0
            # documento (nº do DDA) entra no hash: dois boletos idênticos no mesmo dia
            # (mesmo favorecido/CNPJ/valor) têm documentos distintos — sem ele um deles
            # colidia e era pulado, deixando o dia sem fechar (fix 29/06).
            doc = str(b.get(c_doc)).strip() if c_doc else ""
            h = hashlib.sha256(f"dda-detalhe|{dia}|{b.get(c_cnpj)}|{fav}|{b['_valor']:.2f}|{doc}"
                               .encode()).hexdigest()
            if query_one("SELECT 1 FROM lancamentos WHERE linha_hash=?", (h,)):
                continue
            execute(
                "INSERT INTO lancamentos (empresa_id, conta_bancaria_id, data, descricao, "
                "contraparte, cnpj_contraparte, documento, valor, tipo, plano_conta_id, "
                "classificado, origem, linha_hash, observacao) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (emp_id, conta_id, dia, f"BOLETO DDA - {fav}", fav, _digitos(b.get(c_cnpj)),
                 (str(b.get(c_doc)).strip() if c_doc else None) or None, float(b["_valor"]), "saida",
                 pid, classif, "dda-detalhe", h,
                 "Detalhe do pagamento DDA (relatorio Safra Por Pagamentos)"))
            total_inseridos += 1
    print(f"\n[COMMIT] {total_inseridos} boleto(s) detalhado(s) em {len(a_processar)} dia(s).")


if __name__ == "__main__":
    commit = "--commit" in sys.argv
    sem_fallback = "--sem-fallback" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    main(args[0], commit, sem_fallback)
