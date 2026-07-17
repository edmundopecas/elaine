"""
Detalhar Boletos DDA — sobe o relatório do Safra "Pagamentos → Consulta e
Relatórios → Por Pagamentos" (cada boleto pago com data + valor liquidado +
favorecido). O app agrupa por dia, confere com a linha somada do extrato
("PAGAMENTO DE BOLETO DDA") e, ao confirmar, troca a linha somada pelos boletos
itemizados — já classificados pela Base de Tipos (sobrando, vira Mercadoria).
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter

import pandas as pd
import streamlit as st

from classificador import classificar_movimento, regras_ativas
from db import execute, query, query_one

st.title("📄 Detalhar Boletos DDA")
st.caption("Suba o relatório **Por Pagamentos** do Safra. Eu confiro dia a dia "
           "com o extrato e troco a linha somada pelos boletos, com fornecedor "
           "e categoria.")


def _norm(s) -> str:
    s = "" if s is None else str(s)
    s = unicodedata.normalize("NFKD", s).encode("ASCII", "ignore").decode()
    return re.sub(r"\s+", " ", s).strip().lower()


def _digitos(s) -> str:
    return re.sub(r"\D", "", str(s or ""))


def _achar_cabecalho(bruto: pd.DataFrame) -> int | None:
    """Acha a linha de cabeçalho (a que tem 'Data' e 'Favorecido')."""
    for i in range(min(30, len(bruto))):
        celulas = [_norm(x) for x in bruto.iloc[i].tolist()]
        if any(c == "data" for c in celulas) and any("favorecido" in c or "beneficiario" in c
                                                     for c in celulas):
            return i
    return None


def _coluna(cols: list[str], *chaves: str) -> str | None:
    for c in cols:
        n = _norm(c)
        if any(k in n for k in chaves):
            return c
    return None


# CMV é o destino padrão dos boletos DDA (são quase todos fornecedor de peças)
CMV = query_one("SELECT id FROM plano_contas WHERE nome LIKE '%Mercadoria%' "
                "AND tipo='despesa' ORDER BY ordem LIMIT 1")
CMV_ID = CMV["id"] if CMV else None

empresas = query("SELECT id, apelido FROM empresas WHERE ativa=1 ORDER BY apelido")
# Conta de fallback por empresa: a do Safra (é de lá que vêm os boletos DDA). Sem o
# ORDER BY, a empresa ficava com a ÚLTIMA conta que o banco devolvesse — a ordem muda
# quando contas_bancarias sofre UPDATE, e os boletos caíam no Itaú/Santander.
contas: dict[int, int] = {}
for c in query("SELECT id, empresa_id, banco FROM contas_bancarias "
               "ORDER BY (CASE WHEN banco LIKE 'Safra%' THEN 0 ELSE 1 END), id"):
    contas.setdefault(c["empresa_id"], c["id"])

arquivo = st.file_uploader("Relatório Por Pagamentos (.xlsx)", type=["xlsx", "xls"])
if not arquivo:
    st.info("Exporte no Safra: **Pagamentos → Consulta e Relatórios → Por Pagamentos**, "
            "um período (ex.: 01/06 a 09/06) e uma conta por vez.")
    st.stop()

bruto = pd.read_excel(arquivo, header=None)
hi = _achar_cabecalho(bruto)
if hi is None:
    st.error("Não reconheci o cabeçalho (esperava colunas 'Data' e 'Favorecido'). "
             "Confirme que é o relatório **Por Pagamentos**.")
    st.stop()

df = pd.read_excel(arquivo, header=hi)
cols = list(df.columns)
c_data = _coluna(cols, "data")  # primeira "data" = data do pagamento
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
    st.warning("Nenhum boleto DDA pago encontrado no arquivo.")
    st.stop()

df["_dia"] = pd.to_datetime(df[c_data], dayfirst=True).dt.strftime("%Y-%m-%d")

# Auto-detecta a empresa pelos dígitos da conta no topo do arquivo
texto_topo = " ".join(_digitos(x) for x in bruto.head(hi).to_numpy().ravel())
emp_auto = None
for r in query("SELECT empresa_id, descricao FROM contas_bancarias"):
    conta_dig = _digitos(r["descricao"])
    if conta_dig and conta_dig[-7:] in texto_topo:
        emp_auto = r["empresa_id"]
        break
idx_default = next((i for i, e in enumerate(empresas) if e["id"] == emp_auto), 0)
sel = st.selectbox("Empresa / conta deste arquivo",
                   [e["apelido"] for e in empresas], index=idx_default)
emp_id = next(e["id"] for e in empresas if e["apelido"] == sel)
conta_id = contas.get(emp_id)

st.success(f"**{len(df)}** boletos DDA pagos · **{df['_valor'].sum():,.2f}** no total · "
           f"empresa **{sel}**" + ("  (detectada automaticamente)" if emp_auto == emp_id else ""))

# ── Confronto dia a dia com o extrato ────────────────────────────────────────
linhas = []
for dia, g in df.groupby("_dia"):
    plan = g["_valor"].sum()
    det = query_one("SELECT COUNT(*) n, COALESCE(SUM(valor),0) v FROM lancamentos "
                    "WHERE empresa_id=? AND data=? AND origem='dda-detalhe'", (emp_id, dia))
    ja, ja_valor = det["n"], float(det["v"])
    somado = query_one(
        "SELECT COALESCE(SUM(valor),0) v FROM lancamentos WHERE empresa_id=? AND data=? "
        "AND origem='extrato' AND UPPER(descricao) LIKE '%BOLETO DDA%'", (emp_id, dia))["v"]
    if ja and plan - ja_valor > 0.01:
        # dia detalhado por um relatório que veio incompleto: dá pra completar,
        # o insert pula por linha_hash o que já está lá (não duplica)
        status = f"🔧 incompleto — faltam R$ {plan - ja_valor:,.2f}"
    elif ja and ja_valor - plan > 0.01:
        status = f"⚠️ sistema tem R$ {ja_valor - plan:,.2f} a mais que o relatório"
    elif ja:
        status = "✅ já detalhado"
    elif somado == 0:
        status = "⚠️ sem linha no extrato"
    elif abs(somado - plan) < 0.01:
        status = "🟢 bate certinho"
    else:
        status = f"🟡 difere R$ {plan - somado:,.2f}"
    linhas.append({"Dia": pd.to_datetime(dia).strftime("%d/%m/%Y"), "_dia": dia,
                   "Boletos": len(g), "Total planilha": plan,
                   "No extrato (somado)": somado, "Status": status})

resumo = pd.DataFrame(linhas)
st.dataframe(
    resumo.drop(columns=["_dia"]),
    column_config={"Total planilha": st.column_config.NumberColumn(format="R$ %.2f"),
                   "No extrato (somado)": st.column_config.NumberColumn(format="R$ %.2f")},
    hide_index=True, use_container_width=True)

a_processar = [r for r in linhas if r["Status"] in ("🟢 bate certinho",)
               or r["Status"].startswith("🟡") or r["Status"].startswith("🔧")]
ja_feitos = [r for r in linhas if r["Status"] == "✅ já detalhado"]
if ja_feitos:
    st.caption(f"{len(ja_feitos)} dia(s) já detalhado(s) serão ignorados (sem duplicar).")

if not a_processar:
    st.info("Nada novo pra importar neste arquivo.")
    st.stop()

if st.button(f"✅ Detalhar {len(a_processar)} dia(s) no sistema", type="primary"):
    regras = regras_ativas()
    total_inseridos = 0
    for r in a_processar:
        dia = r["_dia"]
        # os boletos herdam a conta da linha somada que estão substituindo
        somada = query_one(
            "SELECT conta_bancaria_id c FROM lancamentos WHERE empresa_id=? AND data=? "
            "AND origem='extrato' AND UPPER(descricao) LIKE '%BOLETO DDA%' "
            "ORDER BY conta_bancaria_id LIMIT 1", (emp_id, dia))
        # dia já detalhado (completando o que faltou): a linha somada não existe mais,
        # então herda a conta dos boletos que já estão naquele dia
        detalhado = query_one(
            "SELECT conta_bancaria_id c FROM lancamentos WHERE empresa_id=? AND data=? "
            "AND origem='dda-detalhe' ORDER BY id LIMIT 1", (emp_id, dia))
        if somada:
            conta_do_dia = somada["c"]
        elif detalhado:
            conta_do_dia = detalhado["c"]
        else:
            conta_do_dia = conta_id
        # apaga a(s) linha(s) somada(s) do extrato daquele dia
        execute("DELETE FROM lancamentos WHERE empresa_id=? AND data=? AND origem='extrato' "
                "AND UPPER(descricao) LIKE '%BOLETO DDA%'", (emp_id, dia))

        # Idempotente por HASH: recomputa o hash de cada boleto — ordinal por ocorrência
        # de (favorecido, valor) no relatório (boletos idênticos não colidem) + empresa no
        # hash (Matriz e Filial não colidem entre si) — e só insere o que ainda NÃO está na
        # base. Antes eu comparava com o contraparte GRAVADO, mas ele pode vir truncado/
        # diferente do favorecido do relatório: a comparação falhava, o insert era tentado
        # de novo e o hash colidia (UniqueViolation ao reprocessar um dia já detalhado).
        # Checar o hash direto é imune a isso e deixa o reprocessamento 100% seguro.
        visto = Counter()
        for _, b in df[df["_dia"] == dia].iterrows():
            fav = str(b[c_fav]).strip()
            chave = (fav, round(float(b["_valor"]), 2))
            visto[chave] += 1
            h = hashlib.sha256(
                f"dda-detalhe|{emp_id}|{dia}|{b.get(c_cnpj)}|{fav}|{b['_valor']:.2f}"
                f"|#{visto[chave]}".encode()).hexdigest()
            if query_one("SELECT 1 FROM lancamentos WHERE linha_hash=?", (h,)):
                continue  # já inserido (dia completo ou reprocessamento)
            regra = classificar_movimento(fav, "saida", emp_id, regras)
            pid = regra["plano_conta_id"] if regra else CMV_ID
            execute(
                "INSERT INTO lancamentos (empresa_id, conta_bancaria_id, data, descricao, "
                "contraparte, cnpj_contraparte, documento, valor, tipo, plano_conta_id, "
                "classificado, origem, linha_hash, observacao) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (emp_id, conta_do_dia, dia, f"BOLETO DDA - {fav}", fav, _digitos(b.get(c_cnpj)),
                 (str(b.get(c_doc)).strip() if c_doc else None) or None, float(b["_valor"]), "saida",
                 pid, 1, "dda-detalhe", h,
                 "Detalhe do pagamento DDA (relatório Safra Por Pagamentos)"))
            total_inseridos += 1
    st.success(f"Feito! {total_inseridos} boleto(s) detalhado(s) em {len(a_processar)} dia(s). "
               f"Veja na tela de **Saídas**.")
    st.balloons()
