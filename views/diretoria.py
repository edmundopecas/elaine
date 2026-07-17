"""
Visão da Diretoria — leitura financeira em linguagem direta, pra reunião.

Quatro abas, todas calculadas com o dado real já classificado no Elaine:
  1. DRE em português claro   — está ganhando ou perdendo, e onde está o ralo
  2. Onde o dinheiro escapa    — maiores vazamentos + custos invisíveis
  3. Investimento × Desperdício — cada gasto: gera receita / mantém / só drena
  4. Painel de 5 minutos       — 6 indicadores com faixa saudável e sinal

Nada de jargão: cada número vem com uma frase que diz o que fazer.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date

import altair as alt
import pandas as pd
import streamlit as st

from db import query
from tema import POSITIVO as VERDE, NEGATIVO as VERMELHO, ATENCAO as AZUL, NEUTRO as CINZA


def brl(v: float) -> str:
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def pct(v: float) -> str:
    return f"{v:.1f}%".replace(".", ",")


st.title("📋 Visão da Diretoria")
st.caption("Leitura do negócio em português claro — sem jargão de contabilidade. "
           "Tudo vem do extrato bancário já classificado.")

# ── Filtros: período + empresa ────────────────────────────────────────────────
ext = query("SELECT MIN(data) lo, MAX(data) hi FROM lancamentos")[0]
lo = pd.to_datetime(ext["lo"]).date() if ext["lo"] else date.today()
hi = pd.to_datetime(ext["hi"]).date() if ext["hi"] else date.today()

c1, c2 = st.columns([2, 1])
periodo = c1.date_input("Período", value=(lo, hi), min_value=lo, max_value=hi,
                        format="DD/MM/YYYY")
if isinstance(periodo, (list, tuple)):
    d_ini, d_fim = (periodo[0], periodo[-1]) if periodo else (lo, hi)
else:
    d_ini = d_fim = periodo

empresas = query("SELECT id, apelido FROM empresas ORDER BY apelido")
op_emp = ["Grupo todo (consolidado)"] + [e["apelido"] for e in empresas]
sel_emp = c2.selectbox("Empresa", op_emp)

par: list = [d_ini.isoformat(), d_fim.isoformat()]
emp_sql = ""
if sel_emp != op_emp[0]:
    emp_id = next(e["id"] for e in empresas if e["apelido"] == sel_emp)
    emp_sql = " AND l.empresa_id = ?"
    par.append(emp_id)
par_t = tuple(par)

# ── Agregados-base (uma consulta de despesa por categoria + receita) ───────────
cats = query(
    f"""SELECT p.id, p.nome, p.grupo, SUM(l.valor) v, COUNT(*) n
        FROM lancamentos l JOIN plano_contas p ON p.id = l.plano_conta_id
        WHERE l.tipo='saida' AND p.entra_dre=1 AND l.data BETWEEN ? AND ?{emp_sql}
        GROUP BY p.id, p.nome, p.grupo""", par_t)

rec_row = query(
    f"""SELECT COALESCE(SUM(l.valor),0) v FROM lancamentos l
        JOIN plano_contas p ON p.id=l.plano_conta_id
        WHERE l.tipo='entrada' AND p.entra_dre=1 AND l.data BETWEEN ? AND ?{emp_sql}""", par_t)
receita = float(rec_row[0]["v"])

# retiradas dos sócios (pró-labore entra na DRE; gastos pessoais ficam fora)
socios_fora = query(
    f"""SELECT COALESCE(SUM(l.valor),0) v FROM lancamentos l
        JOIN plano_contas p ON p.id=l.plano_conta_id
        WHERE l.tipo='saida' AND p.grupo='Gastos Pessoais (Sócios)'
          AND l.data BETWEEN ? AND ?{emp_sql}""", par_t)[0]["v"]
saque_socios = query(
    f"""SELECT COALESCE(SUM(l.valor),0) v FROM lancamentos l
        WHERE l.plano_conta_id=34 AND l.tipo='saida'
          AND l.data BETWEEN ? AND ?{emp_sql}""", par_t)[0]["v"]

pend_row = query(
    f"""SELECT COUNT(*) n, COALESCE(SUM(l.valor),0) v FROM lancamentos l
        WHERE l.tipo='saida' AND l.plano_conta_id IS NULL AND l.data BETWEEN ? AND ?{emp_sql}""",
    par_t)[0]

# ── Monta a DRE a partir das categorias ───────────────────────────────────────
por_grupo: dict[str, float] = defaultdict(float)
for c in cats:
    por_grupo[c["grupo"]] += float(c["v"])

deducoes = por_grupo.get("Deduções", 0.0)
custos = por_grupo.get("Custos", 0.0)
op_grupos = {g: v for g, v in por_grupo.items() if g not in ("Deduções", "Custos")}
despesas_op = sum(op_grupos.values())

receita_liq = receita - deducoes
lucro_bruto = receita_liq - custos
resultado = lucro_bruto - despesas_op

margem_bruta = (lucro_bruto / receita * 100) if receita else 0.0
margem_liq = (resultado / receita * 100) if receita else 0.0
pessoal = por_grupo.get("Despesas com Pessoal", 0.0)
financeiras = por_grupo.get("Despesas Financeiras", 0.0)
prolabore = por_grupo.get("Sócios", 0.0)
retiradas_socios = prolabore + float(socios_fora)
# retirada TOTAL dos sócios (pró-labore na DRE + gastos pessoais + saque, fora DRE)
retirada_socios_total = prolabore + float(socios_fora) + float(saque_socios)
# só a parte que drena caixa: gastos pessoais + saque (o pró-labore agora
# conta como parte que MANTÉM a operação, por decisão do Filipe 01/07)
retirada_socios_fora = float(socios_fora) + float(saque_socios)

if receita == 0 and not cats:
    st.info("Sem dados classificados no período/empresa selecionados.")
    st.stop()

t1, t2, t3, t4, t5 = st.tabs([
    "🧾 Resultado de verdade", "🚰 Onde o dinheiro escapa",
    "⚖️ Investimento × Desperdício", "⏱️ Painel de 5 minutos", "👤 Sócios"])

# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — DRE em português claro
# ══════════════════════════════════════════════════════════════════════════════
with t1:
    ganhando = resultado >= 0
    cor = VERDE if ganhando else VERMELHO
    verbo = "GANHANDO" if ganhando else "PERDENDO"
    # maior ralo = maior grupo de despesa que NÃO é a mercadoria (CMV é esperado)
    ralo_grupo = max(op_grupos.items(), key=lambda kv: kv[1]) if op_grupos else (None, 0)

    verdict = (
        f"<div style='background:{cor};color:#F3EFE5;padding:18px 22px;border-radius:12px;"
        f"font-size:1.15rem;line-height:1.5'>"
        f"No período, {'o grupo' if sel_emp==op_emp[0] else sel_emp} está "
        f"<b>{verbo} {brl(resultado)}</b> — margem de <b>{pct(margem_liq)}</b> "
        f"sobre tudo que entrou.<br>"
        f"De cada R$ 100 que entram, sobram <b>{brl(margem_liq)}</b> no fim.")
    if ralo_grupo[0]:
        verdict += (f"<br>Maior gasto fora da mercadoria: <b>{ralo_grupo[0]}</b> "
                    f"({brl(ralo_grupo[1])}).")
    verdict += "</div>"
    st.markdown(verdict, unsafe_allow_html=True)
    st.write("")

    k = st.columns(3)
    k[0].metric("Margem bruta", pct(margem_bruta),
                help="O que sobra depois de pagar só a mercadoria vendida.")
    k[1].metric("Margem final (operação)", pct(margem_liq),
                help="O que sobra depois de TODAS as despesas da operação.")
    k[2].metric("Lucro do período", brl(resultado),
                delta="positivo" if ganhando else "negativo",
                delta_color="normal" if ganhando else "inverse")

    # Cascata da DRE, linha a linha, em linguagem simples
    linhas = [
        ("＋ Entrou (vendas + aluguéis + rendimentos)", receita, "receita"),
        ("－ Impostos e devoluções sobre a venda", -deducoes, "deducao"),
        ("＝ Sobrou da venda (receita líquida)", receita_liq, "subtotal"),
        ("－ Mercadoria que você comprou pra revender", -custos, "custo"),
        ("＝ Lucro bruto (antes das contas da casa)", lucro_bruto, "subtotal"),
    ]
    for g, v in sorted(op_grupos.items(), key=lambda kv: -kv[1]):
        linhas.append((f"－ {g}", -v, "despesa"))
    linhas.append(("＝ RESULTADO (o que a operação deixou)", resultado, "final"))

    df = pd.DataFrame([{"Conta": n, "R$": v} for n, v, _ in linhas])
    st.dataframe(
        df, hide_index=True, use_container_width=True,
        column_config={"R$": st.column_config.NumberColumn(format="R$ %.2f")})

    if pend_row["n"]:
        st.caption(f"⚠️ Ainda faltam classificar **{pend_row['n']} saídas** "
                   f"({brl(pend_row['v'])}) — quando entrarem, o resultado desce um pouco.")
    st.caption("Este é o resultado da **operação** (o que o negócio em si gera). "
               "Não desconta o que os sócios tiram nem aplicações — isso está nas "
               "outras abas e no Comparativo.")

# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — Onde o dinheiro escapa
# ══════════════════════════════════════════════════════════════════════════════
with t2:
    st.subheader("🔴 Os 5 maiores gastos (fora a mercadoria)")
    st.caption("A mercadoria (CMV) é o maior gasto, mas é esperado — é o que você "
               "revende. Aqui estão os maiores gastos **da operação** que valem revisar.")

    ranking = sorted(op_grupos.items(), key=lambda kv: -kv[1])[:5]
    base_desp = despesas_op or 1
    rdf = pd.DataFrame([{
        "Gasto": g, "R$ no período": v,
        "% das despesas": v / base_desp * 100,
    } for g, v in ranking])
    ch = (alt.Chart(rdf).mark_bar(cornerRadiusEnd=3, color=VERMELHO)
          .encode(x=alt.X("R$ no período:Q", title=None, axis=alt.Axis(format="~s")),
                  y=alt.Y("Gasto:N", sort=[g for g, _ in ranking], title=None),
                  tooltip=["Gasto:N", alt.Tooltip("R$ no período:Q", format=",.2f")])
          .properties(height=60 + 42 * len(ranking)))
    st.altair_chart(ch, use_container_width=True)
    st.dataframe(rdf, hide_index=True, use_container_width=True, column_config={
        "R$ no período": st.column_config.NumberColumn(format="R$ %.2f"),
        "% das despesas": st.column_config.NumberColumn(format="%.1f%%")})

    st.divider()
    st.subheader("👻 Custos invisíveis (o que sai calado)")
    st.caption("Tarifa de banco, juros, IOF, taxa de cartão, assinatura de sistema — "
               "cada um parece pequeno, mas somados sangram o caixa todo mês.")

    # categorias que costumam passar despercebidas
    ALVOS = {"Outras Despesas Financeiras", "Taxas de Cartão/Adquirente",
             "Aluguel de Maquininha (Cartão)", "Software/Assinaturas",
             "Informática/TI", "Segurança/Vigilância"}
    invis = [(c["nome"], float(c["v"]), c["n"]) for c in cats if c["nome"] in ALVOS]
    invis.sort(key=lambda x: -x[1])
    tot_invis = sum(v for _, v, _ in invis)

    if invis:
        idf = pd.DataFrame([{"Custo": n, "R$ no período": v, "Nº lançamentos": q}
                            for n, v, q in invis])
        st.dataframe(idf, hide_index=True, use_container_width=True, column_config={
            "R$ no período": st.column_config.NumberColumn(format="R$ %.2f")})
        maior = invis[0]
        st.markdown(
            f"<div style='background:{AZUL};color:#F3EFE5;padding:14px 18px;border-radius:10px'>"
            f"💡 <b>{brl(tot_invis)}</b> escaparam em custos silenciosos no período. "
            f"O maior é <b>{maior[0]}</b> ({brl(maior[1])} em {maior[2]} lançamentos) — "
            f"comece por ele: leve pro gerente do banco/adquirente e peça renegociação. "
            f"Cada 1% que cortar aqui volta direto pro lucro.</div>",
            unsafe_allow_html=True)
    else:
        st.info("Nenhum custo silencioso classificado no período.")

# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — Investimento × Desperdício
# ══════════════════════════════════════════════════════════════════════════════
with t3:
    st.caption("Cada gasto entra num de três baldes: **gera receita** (investimento), "
               "**mantém a operação** (necessário) ou **só drena caixa** (revisar). "
               "A classificação é um ponto de partida — me diga se quiser mover algo.")

    GRUPO_BUCKET = {
        "Custos": "gera", "Despesas Comerciais": "gera",
        "Despesas com Pessoal": "mantem", "Ocupação": "mantem",
        "Despesas Administrativas": "mantem", "Tributos": "mantem",
        "Deduções": "mantem", "Construção": "mantem",
        "Sócios": "mantem",  # pró-labore = parte que mantém a operação (decisão Filipe 01/07)
        "Despesas Financeiras": "drena",
    }
    CAT_OVERRIDE = {"Rompimento de Contrato": "drena"}

    buckets = {"gera": [], "mantem": [], "drena": []}
    for c in cats:
        b = CAT_OVERRIDE.get(c["nome"]) or GRUPO_BUCKET.get(c["grupo"], "mantem")
        buckets[b].append((c["nome"], float(c["v"])))
    # Retirada dos sócios que só drena caixa: gastos pessoais pagos pela empresa
    # + saque em dinheiro (o pró-labore ficou no balde "mantém a operação").
    if retirada_socios_fora:
        buckets["drena"].append(
            ("Retirada dos sócios (gastos pessoais + saque)",
             retirada_socios_fora))
    tot = {b: sum(v for _, v in lst) for b, lst in buckets.items()}
    total_desp = sum(tot.values()) or 1

    META = [
        ("gera", "🟢 Gera receita", VERDE,
         "Investimento: compra o que você revende e traz cliente. Cortar aqui = vender menos."),
        ("mantem", "🔵 Mantém a operação", AZUL,
         "Necessário pra funcionar: folha, pró-labore, aluguel, energia, impostos, contador. Otimize, não corte."),
        ("drena", "🔴 Só drena caixa", VERMELHO,
         "Não gera nem sustenta nada: juros, tarifas, multas. É o primeiro alvo de corte/renegociação."),
    ]

    cols = st.columns(3)
    for col, (b, titulo, cor, desc) in zip(cols, META):
        with col:
            st.markdown(f"<div style='background:{cor};color:#F3EFE5;padding:10px 14px;"
                        f"border-radius:10px;text-align:center'><b>{titulo}</b><br>"
                        f"<span style='font-size:1.4rem'>{brl(tot[b])}</span><br>"
                        f"{pct(tot[b]/total_desp*100)} das despesas</div>",
                        unsafe_allow_html=True)
            st.caption(desc)
            for nome, v in sorted(buckets[b], key=lambda x: -x[1]):
                st.write(f"• {nome} — **{brl(v)}**")

    st.divider()
    if tot["drena"]:
        st.markdown(
            f"<div style='background:{VERMELHO};color:#F3EFE5;padding:14px 18px;border-radius:10px'>"
            f"🎯 <b>{brl(tot['drena'])}</b> saíram do caixa sem gerar nem sustentar venda. "
            f"Os juros e tarifas do banco dá pra <b>renegociar</b>; a retirada dos sócios "
            f"(<b>{brl(retirada_socios_fora)}</b>) dá pra <b>planejar</b>. É o primeiro "
            f"lugar pra olhar quando o caixa apertar.</div>", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — Painel de 5 minutos (6 KPIs)
# ══════════════════════════════════════════════════════════════════════════════
with t4:
    st.caption("Os 6 números pra bater o olho toda segunda. 🟢 saudável · 🟡 atenção · "
               "🔴 agir. Se algo estiver 🔴, comece a semana resolvendo aquilo.")

    peso_cmv = (custos / receita * 100) if receita else 0
    peso_folha = (pessoal / receita * 100) if receita else 0
    peso_fin = (financeiras / receita * 100) if receita else 0
    peso_ret = (retiradas_socios / receita * 100) if receita else 0

    def sinal(v, bom, alerta, maior_melhor=True):
        if maior_melhor:
            return "🟢" if v >= bom else ("🟡" if v >= alerta else "🔴")
        return "🟢" if v <= bom else ("🟡" if v <= alerta else "🔴")

    KPIS = [
        ("Margem bruta", pct(margem_bruta), sinal(margem_bruta, 30, 15),
         "🟢 ≥30% · 🔴 <15%", "Depois de pagar a mercadoria, sobra o suficiente pra tocar a casa?"),
        ("Margem final", pct(margem_liq), sinal(margem_liq, 8, 3),
         "🟢 ≥8% · 🔴 <3%", "A operação está realmente deixando lucro, ou só girando dinheiro?"),
        ("Peso da folha", pct(peso_folha), sinal(peso_folha, 12, 20, maior_melhor=False),
         "🟢 ≤12% · 🔴 >20%", "Dá pra vender mais sem aumentar a folha na mesma proporção?"),
        ("Peso da mercadoria (CMV)", pct(peso_cmv), sinal(peso_cmv, 65, 78, maior_melhor=False),
         "🟢 ≤65% · 🔴 >78%", "O preço de compra subiu? Dá pra negociar melhor com fornecedor?"),
        ("Custo do banco", pct(peso_fin), sinal(peso_fin, 1.5, 3, maior_melhor=False),
         "🟢 ≤1,5% · 🔴 >3%", "Quanto o banco leva em juros/tarifas? Quando renegociei por último?"),
        ("Retiradas dos sócios", pct(peso_ret), sinal(peso_ret, 8, 15, maior_melhor=False),
         "🟢 ≤8% · 🔴 >15%", "Os sócios estão tirando dentro do que a empresa aguenta?"),
    ]

    linha = st.columns(3)
    for i, (nome, valor, sig, faixa, pergunta) in enumerate(KPIS):
        with linha[i % 3]:
            st.markdown(f"### {sig} {valor}")
            st.markdown(f"**{nome}**")
            st.caption(f"{faixa}")
            st.caption(f"❓ {pergunta}")
            st.write("")

    st.divider()
    st.markdown(
        f"**Resumo da semana:** o negócio "
        f"{'está lucrando' if resultado>=0 else 'está no vermelho'} "
        f"(**{brl(resultado)}**, margem {pct(margem_liq)}). "
        + ("🔴 Atenção ao **custo do banco** — está alto e é o mais fácil de renegociar."
           if peso_fin > 3 else "Nenhum indicador em vermelho crítico esta semana."))

# ══════════════════════════════════════════════════════════════════════════════
# TAB 5 — Sócios (quanto os donos tiraram, tudo consolidado)
# ══════════════════════════════════════════════════════════════════════════════
with t5:
    st.caption("Tudo que os sócios tiraram da empresa no período — juntando as três "
               "formas: pró-labore, gastos pessoais pagos pela empresa e saque em "
               "dinheiro. Aqui nada fica escondido.")

    # 1) Pró-labore (grupo Sócios, entra na DRE)
    pl = query(
        f"""SELECT l.contraparte nome, SUM(l.valor) v, COUNT(*) n FROM lancamentos l
            JOIN plano_contas p ON p.id=l.plano_conta_id
            WHERE p.grupo='Sócios' AND l.data BETWEEN ? AND ?{emp_sql}
            GROUP BY l.contraparte ORDER BY v DESC""", par_t)
    # 2) Gastos pessoais dos sócios (fora da DRE)
    gp = query(
        f"""SELECT p.nome, SUM(l.valor) v, COUNT(*) n FROM lancamentos l
            JOIN plano_contas p ON p.id=l.plano_conta_id
            WHERE p.grupo='Gastos Pessoais (Sócios)' AND l.data BETWEEN ? AND ?{emp_sql}
            GROUP BY p.nome ORDER BY v DESC""", par_t)
    # 3) Saque em dinheiro (fora da DRE)
    sq = query(
        f"""SELECT COALESCE(SUM(l.valor),0) v, COUNT(*) n FROM lancamentos l
            WHERE l.plano_conta_id=34 AND l.tipo='saida' AND l.data BETWEEN ? AND ?{emp_sql}""",
        par_t)[0]

    tot_pl = sum(float(r["v"]) for r in pl)
    tot_gp = sum(float(r["v"]) for r in gp)
    tot_sq = float(sq["v"])
    tot_socios = tot_pl + tot_gp + tot_sq

    st.markdown(
        f"<div style='background:{AZUL};color:#F3EFE5;padding:18px 22px;border-radius:12px;"
        f"font-size:1.15rem'>👤 Os sócios tiraram <b>{brl(tot_socios)}</b> no período"
        + (f" — o equivalente a <b>{pct(tot_socios/receita*100)}</b> de tudo que entrou "
           f"e a <b>{pct(tot_socios/resultado*100)}</b> do lucro da operação."
           if receita and resultado > 0 else "") + "</div>", unsafe_allow_html=True)
    st.write("")

    k = st.columns(3)
    k[0].metric("💼 Pró-labore", brl(tot_pl), help="Retirada formal (entra na DRE como despesa).")
    k[1].metric("🏠 Gastos pessoais pela empresa", brl(tot_gp),
                help="Boletos particulares dos sócios pagos com dinheiro da empresa (fora da DRE).")
    k[2].metric("💵 Saque em dinheiro", brl(tot_sq), help="Dinheiro sacado (fora da DRE).")

    st.divider()
    ca, cb = st.columns(2)
    with ca:
        st.subheader("💼 Pró-labore por pessoa")
        if pl:
            st.dataframe(
                pd.DataFrame([{"Sócio": r["nome"], "R$": float(r["v"])} for r in pl]),
                hide_index=True, use_container_width=True,
                column_config={"R$": st.column_config.NumberColumn(format="R$ %.2f")})
        else:
            st.info("Sem pró-labore no período.")
    with cb:
        st.subheader("🏠 Gastos pessoais por tipo")
        if gp:
            st.dataframe(
                pd.DataFrame([{"Tipo": r["nome"].replace("Pessoal - ", ""),
                               "R$": float(r["v"])} for r in gp]),
                hide_index=True, use_container_width=True,
                column_config={"R$": st.column_config.NumberColumn(format="R$ %.2f")})
        else:
            st.info("Sem gastos pessoais no período.")

    # Transparência: dinheiro pra sócios classificado em OUTRAS categorias
    NOMES = ["edmilson", "caio ambros", "rosilene", "elaine de lima", "edna maria",
             "edivaldo", "paulo filipe", "tatiane de fatima"]
    like = " OR ".join(f"lower(l.contraparte) LIKE '%{n}%'" for n in NOMES)
    outras = query(
        f"""SELECT p.nome cat, SUM(l.valor) v FROM lancamentos l
            JOIN plano_contas p ON p.id=l.plano_conta_id
            WHERE l.tipo='saida' AND ({like})
              AND p.grupo NOT IN ('Sócios','Gastos Pessoais (Sócios)')
              AND l.data BETWEEN ? AND ?{emp_sql}
            GROUP BY p.nome ORDER BY v DESC""", par_t)
    if outras:
        tot_outras = sum(float(r["v"]) for r in outras)
        det = " · ".join(f"{r['cat']} {brl(float(r['v']))}" for r in outras)
        st.divider()
        st.caption(f"ℹ️ Além disso, **{brl(tot_outras)}** pagos a sócios estão "
                   f"classificados em outras categorias (por decisão sua, não são "
                   f"retirada): {det}.")
