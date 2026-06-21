"""
Saídas — foco único: ver cada SAÍDA e marcar o que foi (mercadoria, fornecedor,
imposto…), com os totais por categoria na hora. Filtros por empresa, mês e dia,
e exportação da planilha em Excel.
"""
from __future__ import annotations

import io
from datetime import date

import pandas as pd
import streamlit as st

from classificador import aprender_regra, reclassificar_pendentes
from db import execute, query

# Históricos genéricos demais pra virarem regra sozinhos (sem fornecedor não dá
# pra saber "do que se trata"): aprender com eles classificaria errado tudo igual.
GENERICOS = {
    "pagamento de boleto dda", "pagamento de boleto", "boleto",
    "pix enviado", "pix enviado transf", "pix", "ted", "doc",
    "transferencia", "saque", "deposito", "debito", "credito",
}

st.title("💸 Saídas — o que foi cada pagamento")
st.caption("Escolha na coluna **O que foi?** o tipo de cada saída — **salva sozinho** "
           "na hora. Os totais por categoria atualizam embaixo.")

# ── Categorias de saída (do plano de contas) ─────────────────────────────────
cats = query("SELECT id, nome FROM plano_contas WHERE ativo=1 "
             "AND tipo IN ('despesa','transferencia') ORDER BY ordem")
nome_para_id = {c["nome"]: c["id"] for c in cats}
NAO_DEF = "— não definido —"
opcoes = [NAO_DEF] + list(nome_para_id.keys())

# ── Filtros: empresa e período (de / até) ────────────────────────────────────
empresas = query("SELECT id, apelido FROM empresas WHERE ativa=1 ORDER BY apelido")
ext = query("SELECT MIN(data) lo, MAX(data) hi FROM lancamentos WHERE tipo='saida'")[0]
lo = pd.to_datetime(ext["lo"]).date() if ext["lo"] else date.today()
hi = pd.to_datetime(ext["hi"]).date() if ext["hi"] else date.today()

def _parse_valor_br(s: str) -> float | None:
    """Aceita '1.411,46', '1411,46', '1411.46' ou '597'."""
    s = s.strip().replace("R$", "").replace(" ", "")
    if not s:
        return None
    if "," in s:                       # formato BR: vírgula é decimal, ponto é milhar
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


c1, c2, c3 = st.columns([1, 1, 2])
sel_emp = c1.selectbox("Empresa", ["Todas"] + [e["apelido"] for e in empresas])
sel_cat = c2.selectbox("Categoria", ["Todas"] + opcoes)
periodo = c3.date_input("Período (de / até)", value=(lo, hi),
                        min_value=lo, max_value=hi, format="DD/MM/YYYY")

c4, c5 = st.columns([2, 1])
busca = c4.text_input("🔎 Buscar fornecedor / descrição", placeholder="ex: braga, icms, posto…")
busca_valor = c5.text_input("💰 Valor exato (R$)", placeholder="ex: 1.411,46")

# date_input devolve 1 data enquanto o usuário não escolhe a 2ª — trata os dois casos
if isinstance(periodo, (list, tuple)):
    d_ini, d_fim = (periodo[0], periodo[-1]) if periodo else (lo, hi)
else:
    d_ini = d_fim = periodo

cond, params = ["l.tipo='saida'"], []
if sel_emp != "Todas":
    emp_id = next(e["id"] for e in empresas if e["apelido"] == sel_emp)
    cond.append("l.empresa_id=?"); params.append(emp_id)
# Filtro por categoria: "— não definido —" pega os sem categoria (NULL)
if sel_cat == NAO_DEF:
    cond.append("l.plano_conta_id IS NULL")
elif sel_cat != "Todas":
    cond.append("l.plano_conta_id=?"); params.append(nome_para_id[sel_cat])
cond.append("l.data BETWEEN ? AND ?")
params += [d_ini.isoformat(), d_fim.isoformat()]
if busca.strip():
    cond.append("(LOWER(COALESCE(l.contraparte,'')) LIKE ? OR LOWER(COALESCE(l.descricao,'')) LIKE ?)")
    b = f"%{busca.strip().lower()}%"; params += [b, b]
valor_alvo = _parse_valor_br(busca_valor)
if valor_alvo is not None:
    cond.append("ABS(l.valor - ?) < 0.005"); params.append(valor_alvo)
where = " AND ".join(cond)

# ── Métricas-resumo no topo ──────────────────────────────────────────────────
total = query(f"SELECT COALESCE(SUM(valor),0) v FROM lancamentos l WHERE {where}",
              tuple(params))[0]["v"]
mercadoria = query(
    f"SELECT COALESCE(SUM(l.valor),0) v FROM lancamentos l "
    f"JOIN plano_contas p ON p.id=l.plano_conta_id "
    f"WHERE {where} AND p.nome LIKE '%Mercadoria%'", tuple(params))[0]["v"]
nao_def = query(
    f"SELECT COALESCE(SUM(valor),0) v, COUNT(*) n FROM lancamentos l "
    f"WHERE {where} AND l.plano_conta_id IS NULL", tuple(params))[0]

m1, m2, m3 = st.columns(3)
m1.metric("Total de saídas", f"R$ {total:,.2f}")
m2.metric("🛒 Compra de mercadoria", f"R$ {mercadoria:,.2f}")
m3.metric("❓ Falta definir", f"R$ {nao_def['v']:,.2f}", f"{nao_def['n']} itens",
          delta_color="off")

st.divider()

# ── Planilha de saídas (editável) ────────────────────────────────────────────
saidas = query(
    f"""SELECT l.id, l.data AS Data, e.apelido AS Empresa,
        COALESCE(cb.descricao, cb.banco, '—') AS Conta,
        COALESCE(l.contraparte, l.descricao, '(sem descrição)') AS Descrição,
        l.valor AS Valor, COALESCE(p.nome, '{NAO_DEF}') AS Categoria
        FROM lancamentos l
        LEFT JOIN empresas e ON e.id=l.empresa_id
        LEFT JOIN contas_bancarias cb ON cb.id=l.conta_bancaria_id
        LEFT JOIN plano_contas p ON p.id=l.plano_conta_id
        WHERE {where} ORDER BY l.data DESC, l.valor DESC""",
    tuple(params),
)

if not saidas:
    st.info("Nenhuma saída encontrada com esses filtros. "
            "Importe um extrato em **Importar Extrato**.")
    st.stop()

# Dados extras (escondidos do editor) pra base aprender: fornecedor e tipo
meta = {r["id"]: r for r in query(
    f"SELECT l.id, l.contraparte, l.descricao, l.tipo FROM lancamentos l "
    f"WHERE {where}", tuple(params))}

df = pd.DataFrame(saidas)

cab, exp = st.columns([3, 1])
cab.markdown(f"**{len(df)} saídas** — comece pelas maiores (estão no topo):")

# Exportar a planilha filtrada em Excel
buf = io.BytesIO()
with pd.ExcelWriter(buf, engine="openpyxl") as w:
    df.drop(columns=["id"]).to_excel(w, index=False, sheet_name="Saídas")
nome_arq = (f"saidas_{sel_emp}_{d_ini.isoformat()}_a_{d_fim.isoformat()}"
            .replace(" ", "").replace("Todas", "todas"))
exp.download_button("⬇️ Exportar Excel", buf.getvalue(), file_name=f"{nome_arq}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True)

# Editor com key versionada: depois de salvar, incremento a versão pra resetar o
# estado do editor (evita reaplicar/perder edições — causa do "não fica fixo").
ver = st.session_state.setdefault("saidas_editor_ver", 0)
edit = st.data_editor(
    df,
    column_config={
        "id": None,
        "Data": st.column_config.TextColumn("Data", disabled=True, width="small"),
        "Empresa": st.column_config.TextColumn("Empresa", disabled=True, width="small"),
        "Conta": st.column_config.TextColumn("Conta (banco)", disabled=True, width="medium"),
        "Descrição": st.column_config.TextColumn("Descrição", disabled=True, width="large"),
        "Valor": st.column_config.NumberColumn("Valor", format="R$ %.2f", disabled=True),
        "Categoria": st.column_config.SelectboxColumn(
            "O que foi?", options=opcoes, width="medium", required=False),
    },
    hide_index=True, use_container_width=True, num_rows="fixed",
    key=f"saidas_editor_{ver}", height=520,
)

# Auto-salva: cada escolha na coluna "O que foi?" grava na hora (sem botão).
mudancas = st.session_state.get(f"saidas_editor_{ver}", {}).get("edited_rows", {})
if mudancas:
    alterados = aprendidos = 0
    for pos, campos in mudancas.items():
        if "Categoria" not in campos:
            continue
        try:
            lid = int(df.iloc[int(pos)]["id"])
        except (IndexError, ValueError, KeyError):
            continue
        pid = nome_para_id.get(campos["Categoria"])  # None = "— não definido —"
        execute("UPDATE lancamentos SET plano_conta_id=?, classificado=? WHERE id=?",
                (pid, 1 if pid else 0, lid))
        alterados += 1
        # Aprende: guarda na base que esse fornecedor é dessa categoria
        info = meta.get(lid, {})
        chave = (info.get("contraparte") or "").strip()
        if not chave and (info.get("descricao") or "").strip().lower() not in GENERICOS:
            chave = (info.get("descricao") or "").strip()
        if pid and chave:
            aprender_regra(chave, pid, info.get("tipo"))
            aprendidos += 1
    if alterados:
        auto = reclassificar_pendentes() if aprendidos else 0
        st.session_state["saidas_editor_ver"] = ver + 1   # reseta o editor
        msg = f"Salvo! {alterados} ajuste(s)"
        if auto:
            msg += f" · {auto} classificados pela base"
        st.toast(msg, icon="✅")
        st.rerun()

# ── Totais por GRUPO (visão consolidada: ex. "Despesas com Pessoal") ──────────
st.divider()
st.subheader("👥 Total por grupo")
st.caption("Visão consolidada: cada grupo soma suas categorias. "
           "Ex.: **Despesas com Pessoal** junta salários, 13º, férias, rescisão, "
           "pensão e encargos — o custo total com a folha.")
grupos = query(
    f"""SELECT COALESCE(p.grupo, '(sem grupo)') AS Grupo, COUNT(*) AS Qtd,
        SUM(l.valor) AS Total
        FROM lancamentos l LEFT JOIN plano_contas p ON p.id=l.plano_conta_id
        WHERE {where} GROUP BY p.grupo ORDER BY Total DESC""",
    tuple(params),
)
gru_df = pd.DataFrame(grupos)

# Destaque do gasto com pessoal como métrica no topo do bloco
pessoal = next((g for g in grupos if g["Grupo"] == "Despesas com Pessoal"), None)
if pessoal:
    st.metric("👥 Gasto com Pessoal (folha)", f"R$ {pessoal['Total']:,.2f}",
              f"{pessoal['Qtd']} pagamentos", delta_color="off")

gru_df["Total"] = gru_df["Total"].map(lambda v: f"R$ {v:,.2f}")
st.dataframe(gru_df, use_container_width=True, hide_index=True)

# ── Totais por categoria ─────────────────────────────────────────────────────
st.divider()
st.subheader("📊 Total de saídas por categoria")
resumo = query(
    f"""SELECT COALESCE(p.nome, '{NAO_DEF}') AS Categoria, COUNT(*) AS Qtd,
        SUM(l.valor) AS Total
        FROM lancamentos l LEFT JOIN plano_contas p ON p.id=l.plano_conta_id
        WHERE {where} GROUP BY l.plano_conta_id ORDER BY Total DESC""",
    tuple(params),
)
res_df = pd.DataFrame(resumo)
res_df["Total"] = res_df["Total"].map(lambda v: f"R$ {v:,.2f}")
st.dataframe(res_df, use_container_width=True, hide_index=True)
