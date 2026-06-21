"""
Entradas — foco: ver cada ENTRADA e marcar o que foi (venda, transferência entre
empresas, resgate de aplicação…), separando o **faturamento real** (vai pra DRE)
do **movimento interno** (dinheiro que só circulou entre as contas do grupo).

Espelha a tela de Saídas, com uma diferença de propósito:
  • As categorias são as de ENTRADA (receita + movimentações internas).

APRENDE regra ao classificar (igual Saídas), mas com cuidado pra não virar ruído:
só aprende quando a contraparte é um NOME específico (cliente/inquilino recorrente)
e a regra é ESCOPADA À EMPRESA — descritores genéricos ("PIX Recebido", "Cobrança",
"Depósito"…) ficam na lista GENERICOS e não viram regra. Assim recebedor que repete
todo mês (inquilino, CIELO) cai sozinho, sem classificar errado os clientes avulsos.
"""
from __future__ import annotations

import io
from datetime import date

import pandas as pd
import streamlit as st

from classificador import aprender_regra, reclassificar_pendentes
from db import execute, query

# Descritores genéricos demais pra virar regra (sem nome do pagador não dá pra saber
# "de quem é"): aprender com eles classificaria toda entrada igual.
GENERICOS = {
    "pix recebido", "pix - recebido", "pix", "cobranca", "cobranca adiantamento",
    "deposito", "dep dinheiro inter ag", "ted", "ted recebido", "doc", "credito",
    "transferencia", "transferencia recebida", "rendimento", "liberacao vinculada",
    "boleto", "cobranca recebida",
}

st.title("💰 Entradas — de onde veio o dinheiro")
st.caption("Escolha na coluna **O que foi?** o tipo de cada entrada — **salva sozinho** "
           "na hora. Em cima, o faturamento real fica separado do que só circulou "
           "entre as contas do grupo.")

# ── Categorias de entrada (receita + movimentações internas) ──────────────────
cats = query("SELECT id, nome FROM plano_contas WHERE ativo=1 "
             "AND tipo IN ('receita','transferencia') ORDER BY ordem")
nome_para_id = {c["nome"]: c["id"] for c in cats}
NAO_DEF = "— não definido —"
opcoes = [NAO_DEF] + list(nome_para_id.keys())

# ── Filtros: empresa, categoria e período (de / até) ──────────────────────────
empresas = query("SELECT id, apelido FROM empresas WHERE ativa=1 ORDER BY apelido")
ext = query("SELECT MIN(data) lo, MAX(data) hi FROM lancamentos WHERE tipo='entrada'")[0]
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
busca = c4.text_input("🔎 Buscar cliente / descrição", placeholder="ex: cielo, pix, liderprev…")
busca_valor = c5.text_input("💰 Valor exato (R$)", placeholder="ex: 1.411,46")

# date_input devolve 1 data enquanto o usuário não escolhe a 2ª — trata os dois casos
if isinstance(periodo, (list, tuple)):
    d_ini, d_fim = (periodo[0], periodo[-1]) if periodo else (lo, hi)
else:
    d_ini = d_fim = periodo

cond, params = ["l.tipo='entrada'"], []
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

# ── Métricas-resumo no topo ───────────────────────────────────────────────────
total = query(f"SELECT COALESCE(SUM(valor),0) v FROM lancamentos l WHERE {where}",
              tuple(params))[0]["v"]
# Faturamento = receita real (categorias que entram na DRE)
faturamento = query(
    f"SELECT COALESCE(SUM(l.valor),0) v FROM lancamentos l "
    f"JOIN plano_contas p ON p.id=l.plano_conta_id "
    f"WHERE {where} AND p.entra_dre=1", tuple(params))[0]["v"]
# Movimento interno = transferências/aplicações que NÃO entram na DRE
interno = query(
    f"SELECT COALESCE(SUM(l.valor),0) v FROM lancamentos l "
    f"JOIN plano_contas p ON p.id=l.plano_conta_id "
    f"WHERE {where} AND p.entra_dre=0", tuple(params))[0]["v"]
nao_def = query(
    f"SELECT COALESCE(SUM(valor),0) v, COUNT(*) n FROM lancamentos l "
    f"WHERE {where} AND l.plano_conta_id IS NULL", tuple(params))[0]

m1, m2, m3, m4 = st.columns(4)
m1.metric("Total de entradas", f"R$ {total:,.2f}")
m2.metric("💰 Faturamento (vendas)", f"R$ {faturamento:,.2f}")
m3.metric("🔄 Movimento interno", f"R$ {interno:,.2f}", "fora da DRE", delta_color="off")
m4.metric("❓ Falta definir", f"R$ {nao_def['v']:,.2f}", f"{nao_def['n']} itens",
          delta_color="off")

st.caption("**Faturamento** = vendas de verdade (entra no resultado). **Movimento "
           "interno** = transferência entre as empresas e resgate de aplicação — "
           "dinheiro que só circulou, não é faturamento.")

st.divider()

# ── Planilha de entradas (editável) ───────────────────────────────────────────
entradas = query(
    f"""SELECT l.id, l.data AS "Data", e.apelido AS "Empresa",
        COALESCE(cb.descricao, cb.banco, '—') AS "Conta",
        COALESCE(l.contraparte, l.descricao, '(sem descrição)') AS "Descrição",
        l.valor AS "Valor", COALESCE(p.nome, '{NAO_DEF}') AS "Categoria"
        FROM lancamentos l
        LEFT JOIN empresas e ON e.id=l.empresa_id
        LEFT JOIN contas_bancarias cb ON cb.id=l.conta_bancaria_id
        LEFT JOIN plano_contas p ON p.id=l.plano_conta_id
        WHERE {where} ORDER BY l.data DESC, l.valor DESC""",
    tuple(params),
)

if not entradas:
    st.info("Nenhuma entrada encontrada com esses filtros. "
            "Importe um extrato em **Importar Extrato**.")
    st.stop()

# Dados extras (escondidos do editor) pra base aprender: contraparte/descrição/empresa
meta = {r["id"]: r for r in query(
    f"SELECT l.id, l.contraparte, l.descricao, l.tipo, l.empresa_id FROM lancamentos l "
    f"WHERE {where}", tuple(params))}

df = pd.DataFrame(entradas)

cab, exp = st.columns([3, 1])
cab.markdown(f"**{len(df)} entradas** — comece pelas maiores (estão no topo):")

# Exportar a planilha filtrada em Excel
buf = io.BytesIO()
with pd.ExcelWriter(buf, engine="openpyxl") as w:
    df.drop(columns=["id"]).to_excel(w, index=False, sheet_name="Entradas")
nome_arq = (f"entradas_{sel_emp}_{d_ini.isoformat()}_a_{d_fim.isoformat()}"
            .replace(" ", "").replace("Todas", "todas"))
exp.download_button("⬇️ Exportar Excel", buf.getvalue(), file_name=f"{nome_arq}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True)

# Editor com key versionada: depois de salvar, incremento a versão pra resetar o
# estado do editor (mesmo padrão da tela de Saídas).
ver = st.session_state.setdefault("entradas_editor_ver", 0)
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
    key=f"entradas_editor_{ver}", height=520,
)

# Auto-salva: cada escolha grava na hora e APRENDE a base (recebedor recorrente →
# categoria, escopado à empresa). Descritores genéricos não viram regra (GENERICOS).
mudancas = st.session_state.get(f"entradas_editor_{ver}", {}).get("edited_rows", {})
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
        # Aprende: guarda que esse recebedor (contraparte) é dessa categoria, só pra
        # essa empresa. Pula contraparte vazia ou genérica (viraria regra-ruído).
        info = meta.get(lid, {})
        chave = (info.get("contraparte") or "").strip()
        if pid and chave and chave.lower() not in GENERICOS:
            aprender_regra(chave, pid, aplica_tipo="entrada",
                           empresa_id=info.get("empresa_id"))
            aprendidos += 1
    if alterados:
        auto = reclassificar_pendentes() if aprendidos else 0
        st.session_state["entradas_editor_ver"] = ver + 1   # reseta o editor
        msg = f"Salvo! {alterados} ajuste(s)"
        if auto:
            msg += f" · {auto} classificados pela base"
        st.toast(msg, icon="✅")
        st.rerun()

# ── Totais por GRUPO ──────────────────────────────────────────────────────────
st.divider()
st.subheader("📦 Total por grupo")
grupos = query(
    f"""SELECT COALESCE(p.grupo, '(sem grupo)') AS "Grupo", COUNT(*) AS "Qtd",
        SUM(l.valor) AS "Total"
        FROM lancamentos l LEFT JOIN plano_contas p ON p.id=l.plano_conta_id
        WHERE {where} GROUP BY p.grupo ORDER BY "Total" DESC""",
    tuple(params),
)
gru_df = pd.DataFrame(grupos)
gru_df["Total"] = gru_df["Total"].map(lambda v: f"R$ {v:,.2f}")
st.dataframe(gru_df, use_container_width=True, hide_index=True)

# ── Totais por categoria ──────────────────────────────────────────────────────
st.divider()
st.subheader("📊 Total de entradas por categoria")
resumo = query(
    f"""SELECT COALESCE(p.nome, '{NAO_DEF}') AS "Categoria", COUNT(*) AS "Qtd",
        SUM(l.valor) AS "Total"
        FROM lancamentos l LEFT JOIN plano_contas p ON p.id=l.plano_conta_id
        WHERE {where} GROUP BY p.id, p.nome ORDER BY "Total" DESC""",
    tuple(params),
)
res_df = pd.DataFrame(resumo)
res_df["Total"] = res_df["Total"].map(lambda v: f"R$ {v:,.2f}")
st.dataframe(res_df, use_container_width=True, hide_index=True)
