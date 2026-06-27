"""Pendências — só o que ainda NÃO tem categoria (plano_conta_id IS NULL), pra
fechar rápido. Período padrão 15→18; foco em Saídas (dá pra trocar). Edita igual
à tela de Saídas: escolhe "O que foi?", salva sozinho e aprende a regra.
"""
from __future__ import annotations

import io
from datetime import date

import pandas as pd
import streamlit as st

from classificador import aprender_regra, reclassificar_pendentes
from db import execute, query

# Históricos genéricos demais pra virarem regra (mesma lista da tela de Saídas).
GENERICOS = {
    "pagamento de boleto dda", "pagamento de boleto", "boleto",
    "pix enviado", "pix enviado transf", "pix", "ted", "doc",
    "transferencia", "saque", "deposito", "debito", "credito",
}

st.title("⏳ Pendências — o que falta classificar")
st.caption("Mostra só os lançamentos **sem categoria**. Escolha em **O que foi?** — "
           "salva sozinho e aprende pro próximo. Padrão: dia 15 a 18, Saídas.")

# ── Categorias (do plano de contas) ──────────────────────────────────────────
cats = query("SELECT id, nome FROM plano_contas WHERE ativo=1 "
             "AND tipo IN ('despesa','transferencia','receita') ORDER BY ordem")
nome_para_id = {c["nome"]: c["id"] for c in cats}
NAO_DEF = "— não definido —"
opcoes = [NAO_DEF] + list(nome_para_id.keys())

# ── Período: padrão 15→18 do mês dos dados ───────────────────────────────────
empresas = query("SELECT id, apelido FROM empresas WHERE ativa=1 ORDER BY apelido")
ext = query("SELECT MIN(data) lo, MAX(data) hi FROM lancamentos")[0]
lo = pd.to_datetime(ext["lo"]).date() if ext["lo"] else date.today()
hi = pd.to_datetime(ext["hi"]).date() if ext["hi"] else date.today()
# default 15→18 do mês/ano do fim dos dados, preso dentro do intervalo existente
def_ini = min(max(date(hi.year, hi.month, 15), lo), hi)
def_fim = min(max(date(hi.year, hi.month, 18), lo), hi)

c1, c2, c3 = st.columns([1, 1, 2])
sel_tipo = c1.radio("Tipo", ["Saídas", "Entradas", "Todas"], horizontal=False)
sel_emp = c2.selectbox("Empresa", ["Todas"] + [e["apelido"] for e in empresas])
periodo = c3.date_input("Período (de / até)", value=(def_ini, def_fim),
                        min_value=lo, max_value=hi, format="DD/MM/YYYY")

busca = st.text_input("🔎 Buscar fornecedor / descrição", placeholder="ex: amil, posto, valdeval…")

# date_input devolve 1 data enquanto não escolhe a 2ª — trata os dois casos
if isinstance(periodo, (list, tuple)):
    d_ini, d_fim = (periodo[0], periodo[-1]) if periodo else (def_ini, def_fim)
else:
    d_ini = d_fim = periodo

# ── WHERE: SEMPRE só pendentes (sem categoria) ───────────────────────────────
cond = ["l.plano_conta_id IS NULL", "l.data BETWEEN ? AND ?"]
params: list = [d_ini.isoformat(), d_fim.isoformat()]
if sel_tipo == "Saídas":
    cond.append("l.tipo='saida'")
elif sel_tipo == "Entradas":
    cond.append("l.tipo='entrada'")
if sel_emp != "Todas":
    emp_id = next(e["id"] for e in empresas if e["apelido"] == sel_emp)
    cond.append("l.empresa_id=?"); params.append(emp_id)
if busca.strip():
    cond.append("(LOWER(COALESCE(l.contraparte,'')) LIKE ? OR LOWER(COALESCE(l.descricao,'')) LIKE ?)")
    b = f"%{busca.strip().lower()}%"; params += [b, b]
where = " AND ".join(cond)

# ── Métricas ─────────────────────────────────────────────────────────────────
resumo = query(
    f"""SELECT l.tipo, COUNT(*) n, COALESCE(SUM(l.valor),0) v
        FROM lancamentos l WHERE {where} GROUP BY l.tipo""", tuple(params))
v_sai = next((r["v"] for r in resumo if r["tipo"] == "saida"), 0)
n_sai = next((r["n"] for r in resumo if r["tipo"] == "saida"), 0)
v_ent = next((r["v"] for r in resumo if r["tipo"] == "entrada"), 0)
n_ent = next((r["n"] for r in resumo if r["tipo"] == "entrada"), 0)
m1, m2, m3 = st.columns(3)
m1.metric("💸 Saídas pendentes", f"R$ {v_sai:,.2f}", f"{n_sai} itens", delta_color="off")
m2.metric("💰 Entradas pendentes", f"R$ {v_ent:,.2f}", f"{n_ent} itens", delta_color="off")
m3.metric("Total pendente", f"R$ {v_sai + v_ent:,.2f}", f"{n_sai + n_ent} itens",
          delta_color="off")

st.divider()

# ── Planilha (editável) ──────────────────────────────────────────────────────
rows = query(
    f"""SELECT l.id, l.data AS "Data", e.apelido AS "Empresa",
        COALESCE(cb.descricao, cb.banco, '—') AS "Conta",
        COALESCE(l.contraparte, l.descricao, '(sem descrição)') AS "Descrição",
        l.tipo AS "Tipo", l.valor AS "Valor", '{NAO_DEF}' AS "Categoria"
        FROM lancamentos l
        LEFT JOIN empresas e ON e.id=l.empresa_id
        LEFT JOIN contas_bancarias cb ON cb.id=l.conta_bancaria_id
        WHERE {where} ORDER BY l.tipo, l.data, l.valor DESC""",
    tuple(params),
)

if not rows:
    st.success("🎉 Nada pendente nesse período/filtro. Tudo classificado!")
    st.stop()

# meta escondida pra base aprender (fornecedor + tipo)
meta = {r["id"]: r for r in query(
    f"SELECT l.id, l.contraparte, l.descricao, l.tipo FROM lancamentos l WHERE {where}",
    tuple(params))}

df = pd.DataFrame(rows)

cab, exp = st.columns([3, 1])
cab.markdown(f"**{len(df)} pendente(s)** — comece pelos maiores:")
buf = io.BytesIO()
with pd.ExcelWriter(buf, engine="openpyxl") as w:
    df.drop(columns=["id"]).to_excel(w, index=False, sheet_name="Pendencias")
exp.download_button("⬇️ Exportar Excel", buf.getvalue(),
                    file_name=f"pendencias_{d_ini.isoformat()}_a_{d_fim.isoformat()}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True)

ver = st.session_state.setdefault("pend_editor_ver", 0)
edit = st.data_editor(
    df,
    column_config={
        "id": None,
        "Data": st.column_config.TextColumn("Data", disabled=True, width="small"),
        "Empresa": st.column_config.TextColumn("Empresa", disabled=True, width="small"),
        "Conta": st.column_config.TextColumn("Conta (banco)", disabled=True, width="medium"),
        "Descrição": st.column_config.TextColumn("Descrição", disabled=True, width="large"),
        "Tipo": st.column_config.TextColumn("Tipo", disabled=True, width="small"),
        "Valor": st.column_config.NumberColumn("Valor", format="R$ %.2f", disabled=True),
        "Categoria": st.column_config.SelectboxColumn(
            "O que foi?", options=opcoes, width="medium", required=False),
    },
    hide_index=True, use_container_width=True, num_rows="fixed",
    key=f"pend_editor_{ver}", height=520,
)

mudancas = st.session_state.get(f"pend_editor_{ver}", {}).get("edited_rows", {})
if mudancas:
    alterados = aprendidos = 0
    for pos, campos in mudancas.items():
        if "Categoria" not in campos:
            continue
        try:
            lid = int(df.iloc[int(pos)]["id"])
        except (IndexError, ValueError, KeyError):
            continue
        pid = nome_para_id.get(campos["Categoria"])   # None = continua pendente
        execute("UPDATE lancamentos SET plano_conta_id=?, classificado=? WHERE id=?",
                (pid, 1 if pid else 0, lid))
        alterados += 1
        # Aprende regra SÓ pra saída (entrada vira regra-ruído por cliente — decisão antiga)
        info = meta.get(lid, {})
        if pid and info.get("tipo") == "saida":
            chave = (info.get("contraparte") or "").strip()
            if not chave and (info.get("descricao") or "").strip().lower() not in GENERICOS:
                chave = (info.get("descricao") or "").strip()
            if chave:
                aprender_regra(chave, pid, "saida")
                aprendidos += 1
    if alterados:
        auto = reclassificar_pendentes() if aprendidos else 0
        st.session_state["pend_editor_ver"] = ver + 1
        msg = f"Salvo! {alterados} ajuste(s)"
        if auto:
            msg += f" · {auto} classificados pela base"
        st.toast(msg, icon="✅")
        st.rerun()
