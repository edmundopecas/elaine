"""
Saúde Financeira — a tela que responde "por que o dinheiro está apertando".

Nasceu da análise de agosto/2026: a receita caiu 12%, as compras subiram 30% e
o serviço da dívida comeu o que sobrou. Em vez de repetir a consulta à mão todo
mês, a tela faz sozinha — e com duas regras que impedem o número de mentir:

  1. JANELA JUSTA. O mês corrente está sempre incompleto (banco exporta com
     atraso). Comparar mês parcial com mês cheio dá uma "queda" que não existe.
     A tela descobre até que dia TODAS as contas que exportam já foram
     importadas e compara os meses anteriores no MESMO intervalo de dias.
  2. POR DIA ÚTIL. Uma quinzena com feriado não é igual à outra. Todo
     comparativo é média por dia útil (seg–sex).

As contas que pararam de exportar ficam de fora do comparativo e aparecem em
destaque na tela — número que exclui conta sem avisar é número mentiroso.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date

import altair as alt
import pandas as pd
import streamlit as st

from db import query, execute
from tema import POSITIVO as VERDE, NEGATIVO as VERMELHO, ATENCAO as AZUL, NEUTRO as CINZA

st.title("💊 Saúde Financeira")
st.caption("Receita, compra e dívida na mesma tela — comparando sempre a mesma "
           "quantidade de dias, para o mês parcial não parecer queda.")


# ── Formatação ───────────────────────────────────────────────────────────────
def brl(v) -> str:
    return f"R$ {float(v or 0):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def pct(v) -> str:
    return f"{float(v or 0):.1f}%".replace(".", ",")


def var_pct(novo: float, velho: float):
    return None if not velho else (novo - velho) / velho * 100


def num(v, casas: int = 1, sinal: bool = False) -> str:
    """Número no padrão brasileiro (vírgula decimal), com sinal opcional."""
    fmt = f"{{:+.{casas}f}}" if sinal else f"{{:.{casas}f}}"
    return fmt.format(float(v or 0)).replace(".", ",")


def dias_uteis(mes: str, ate_dia: int) -> int:
    """Dias de semana (seg–sex) do dia 1 até `ate_dia` do mês 'YYYY-MM'."""
    y, m = int(mes[:4]), int(mes[5:7])
    dias = []
    for d in range(1, ate_dia + 1):
        try:
            dia = date(y, m, d)
        except ValueError:
            break
        if dia.weekday() < 5:
            dias.append(dia)
    return len(dias)


def mes_menos(mes: str, n: int) -> str:
    y, m = int(mes[:4]), int(mes[5:7])
    total = y * 12 + (m - 1) - n
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


NOMES_MES = ["jan", "fev", "mar", "abr", "mai", "jun",
             "jul", "ago", "set", "out", "nov", "dez"]


def rotulo(mes: str) -> str:
    return f"{NOMES_MES[int(mes[5:7]) - 1]}/{mes[2:4]}"


# ── Filtros ──────────────────────────────────────────────────────────────────
meses_base = [r["m"] for r in query(
    "SELECT DISTINCT substr(data,1,7) m FROM lancamentos ORDER BY m DESC")]
if not meses_base:
    st.warning("Ainda não há lançamentos importados.")
    st.stop()

c1, c2 = st.columns([1, 2])
mes = c1.selectbox("Mês de referência", meses_base, format_func=rotulo)
empresas = query("SELECT id, apelido FROM empresas ORDER BY apelido")
op_emp = ["Grupo todo (consolidado)"] + [e["apelido"] for e in empresas]
sel_emp = c2.selectbox("Empresa", op_emp)

emp_sql, emp_par = "", []
if sel_emp != op_emp[0]:
    emp_sql = " AND l.empresa_id=?"
    emp_par = [next(e["id"] for e in empresas if e["apelido"] == sel_emp)]


# ── Janela justa: até que dia o mês está completo em TODAS as contas ──────────
cob = query(
    """SELECT l.conta_bancaria_id c, MAX(l.data) ult, COUNT(*) n
       FROM lancamentos l JOIN contas_bancarias cb ON cb.id=l.conta_bancaria_id
       WHERE cb.ativa=1 AND substr(l.data,1,7)=? GROUP BY l.conta_bancaria_id""", (mes,))
todas_ativas = query(
    """SELECT cb.id, cb.banco, cb.descricao, e.apelido,
              (SELECT MAX(data) FROM lancamentos WHERE conta_bancaria_id=cb.id) ult
       FROM contas_bancarias cb JOIN empresas e ON e.id=cb.empresa_id
       WHERE cb.ativa=1 ORDER BY cb.id""")

dia_corte = min((int(r["ult"][8:10]) for r in cob), default=1)
com_mov = {r["c"] for r in cob}
atrasadas = [r for r in todas_ativas if r["id"] not in com_mov]

MESES = [m for m in (mes, mes_menos(mes, 1), mes_menos(mes, 2)) if m in meses_base]
DU = {m: max(dias_uteis(m, dia_corte), 1) for m in MESES}

conta_travou = min(cob, key=lambda r: r["ult"]) if cob else None
if conta_travou:
    dono = next((t for t in todas_ativas if t["id"] == conta_travou["c"]), None)
    nome_travou = f"{dono['apelido']} / {dono['banco']}" if dono else "—"
    st.info(f"**Janela comparável: 01 a {dia_corte:02d}/{mes[5:7]}** "
            f"({DU[mes]} dias úteis). É até onde *todas* as contas que exportam já foram "
            f"importadas — a mais atrasada é **{nome_travou}**, até "
            f"{conta_travou['ult'][8:10]}/{mes[5:7]}. Os meses anteriores são comparados "
            f"exatamente no mesmo intervalo de dias.")
else:
    st.warning("Sem movimento importado no mês selecionado.")
    st.stop()

if atrasadas:
    txt = " · ".join(
        f"{a['apelido']}/{a['banco']} (parou em "
        f"{'nunca importou' if not a['ult'] else a['ult'][8:10] + '/' + a['ult'][5:7]})"
        for a in atrasadas)
    st.warning(f"⚠️ **{len(atrasadas)} conta(s) ativa(s) sem exportar neste mês:** {txt}. "
               "O que elas movimentaram não está em nenhum número desta tela.")


# ── Motor: soma na janela justa ──────────────────────────────────────────────
def soma(m: str, extra_sql: str, extra_par: list | None = None) -> float:
    sql = ("SELECT COALESCE(SUM(l.valor),0) v FROM lancamentos l "
           "LEFT JOIN plano_contas p ON p.id=l.plano_conta_id "
           "WHERE substr(l.data,1,7)=? AND CAST(substr(l.data,9,2) AS INTEGER)<=? "
           + extra_sql + emp_sql)
    par = [m, dia_corte] + (extra_par or []) + emp_par
    return float(query(sql, tuple(par))[0]["v"])


ASPA = "'"
F_RECEITA = " AND l.tipo='entrada' AND p.id IN (1,2)"
F_ALUGUEL = " AND l.tipo='entrada' AND p.id=67"
F_CUSTOS = " AND l.tipo='saida' AND p.grupo='Custos'"
F_PESSOAL = " AND l.tipo='saida' AND p.grupo='Despesas com Pessoal'"
F_ESTRUTURA = (" AND l.tipo='saida' AND p.grupo IN "
               "('Despesas Administrativas','Ocupação','Despesas Comerciais','Construção')")
F_TRIBUTOS = " AND l.tipo='saida' AND p.grupo IN ('Tributos','Deduções')"
F_FIN = " AND l.tipo='saida' AND l.plano_conta_id IN (26,27,28,35)"
F_SOCIOS = " AND l.tipo='saida' AND p.grupo IN ('Sócios','Gastos Pessoais (Sócios)')"
F_DIVIDA = " AND l.tipo='saida' AND l.plano_conta_id=39"

# Adquirentes: o recebimento de cartão não tem plano de contas próprio (cai como
# Receita de Vendas), então é reconhecido pelo nome da bandeira no histórico.
# "Liberação Vinculada" é o Safra liberando cartão ANTECIPADO (confirmado pelo
# Filipe em 18/08) — não traz o nome da adquirente no histórico, então precisa
# entrar na mão, senão o cartão e a antecipação saem subestimados.
ADQUIRENTES = {"Cielo": "cielo", "SafraPay": "safrapay", "Getnet": "getnet",
               "PagSeguro": "pagseguro", "Stone": "stone", "Rede": "redecard",
               "Liberação Vinculada (Safra)": "liberacao vinculada"}
F_CARTAO = (" AND l.tipo='entrada' AND ("
            + " OR ".join(f"LOWER(l.descricao) LIKE {ASPA}%{p}%{ASPA}"
                          for p in ADQUIRENTES.values()) + ")")
# Antecipação = recebível adiantado. Duas formas no grupo: o histórico diz
# "antecipação" (SafraPay na Filial) ou é a Liberação Vinculada (Matriz).
F_ANTECIP = (" AND l.tipo='entrada' AND (LOWER(l.descricao) LIKE '%antecipacao%'"
             " OR LOWER(l.descricao) LIKE '%antecipação%'"
             " OR LOWER(l.descricao) LIKE '%liberacao vinculada%')")

D = {m: {"receita": soma(m, F_RECEITA), "aluguel": soma(m, F_ALUGUEL),
         "custos": soma(m, F_CUSTOS), "pessoal": soma(m, F_PESSOAL),
         "estrutura": soma(m, F_ESTRUTURA), "tributos": soma(m, F_TRIBUTOS),
         "financeiras": soma(m, F_FIN), "socios": soma(m, F_SOCIOS),
         "divida": soma(m, F_DIVIDA), "cartao": soma(m, F_CARTAO),
         "antecip": soma(m, F_ANTECIP)} for m in MESES}
for m in MESES:
    d = D[m]
    d["geracao"] = (d["receita"] + d["aluguel"] - d["custos"] - d["pessoal"]
                    - d["estrutura"] - d["tributos"] - d["financeiras"])
    d["apos_socios"] = d["geracao"] - d["socios"]
    d["sobra"] = d["apos_socios"] - d["divida"]
    d["dia_util"] = d["receita"] / DU[m]
    d["cartao_du"] = d["cartao"] / DU[m]
    d["custo_pct"] = (d["custos"] / d["receita"] * 100) if d["receita"] else 0
    d["antecip_pct"] = (d["antecip"] / d["receita"] * 100) if d["receita"] else 0

ref = D[mes]
ant = D[MESES[1]] if len(MESES) > 1 else None
rot_ant = rotulo(MESES[1]) if len(MESES) > 1 else ""

# ── Cabeçalho: os 4 números que importam ─────────────────────────────────────
k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("💰 Receita por dia útil", brl(ref["dia_util"]),
          None if not ant else f"{num(var_pct(ref['dia_util'], ant['dia_util']), 1, True)}% vs {rot_ant}")
k2.metric("💳 Cartão por dia útil", brl(ref["cartao_du"]),
          None if not ant else f"{num(var_pct(ref['cartao_du'], ant['cartao_du']), 1, True)}% vs {rot_ant}")
k3.metric("📦 Compra sobre a venda", pct(ref["custo_pct"]),
          None if not ant else f"{num(ref['custo_pct'] - ant['custo_pct'], 1, True)} p.p. vs {rot_ant}",
          delta_color="inverse")
k4.metric("⏩ Receita que veio antecipada", pct(ref["antecip_pct"]),
          None if not ant else f"{num(ref['antecip_pct'] - ant['antecip_pct'], 1, True)} p.p. vs {rot_ant}",
          delta_color="inverse",
          help="Quanto da receita da janela é recebível adiantado — venda de ontem "
               "puxada para hoje. Subir esse número é consumir o mês seguinte.")
k5.metric("💧 Sobrou depois de tudo", brl(ref["sobra"]),
          None if not ant else f"{brl(ref['sobra'] - ant['sobra'])} vs {rot_ant}")

st.divider()
t1, t2, t3, t4, t5 = st.tabs(["📉 Receita", "✂️ Venda × Compra", "💧 Geração de caixa",
                              "🏦 Dívida", "🎯 Plano de saída"])

# ═════════════════════════════════════════════════════════════════════════════
# 1. RECEITA
# ═════════════════════════════════════════════════════════════════════════════
with t1:
    st.subheader("A receita caiu ou é impressão do mês incompleto?")
    linhas = []
    for m in MESES:
        d = D[m]
        linhas.append({"Mês": rotulo(m), "Dias úteis": DU[m],
                       "Receita na janela": d["receita"], "Média por dia útil": d["dia_util"],
                       "Cartão": d["cartao"], "Cartão por dia útil": d["cartao_du"],
                       "Antecipado (RV)": d["antecip"],
                       "Receita sem antecipação": d["receita"] - d["antecip"]})
    df = pd.DataFrame(linhas)
    show = df.copy()
    for col in ("Receita na janela", "Média por dia útil", "Cartão", "Cartão por dia útil",
                "Antecipado (RV)", "Receita sem antecipação"):
        show[col] = show[col].map(brl)
    st.dataframe(show, use_container_width=True, hide_index=True)

    graf = pd.DataFrame([{"Mês": rotulo(m), "Origem": "Cartão", "Valor": D[m]["cartao_du"]}
                         for m in MESES]
                        + [{"Mês": rotulo(m), "Origem": "PIX / boleto / demais",
                            "Valor": max(D[m]["dia_util"] - D[m]["cartao_du"], 0)} for m in MESES])
    ch = (alt.Chart(graf).mark_bar()
          .encode(x=alt.X("Mês:N", sort=[rotulo(m) for m in reversed(MESES)], title=None),
                  y=alt.Y("Valor:Q", title="R$ por dia útil"),
                  color=alt.Color("Origem:N", scale=alt.Scale(
                      domain=["Cartão", "PIX / boleto / demais"], range=[VERMELHO, VERDE])),
                  tooltip=["Mês", "Origem", alt.Tooltip("Valor:Q", format=",.2f")])
          .properties(height=260))
    st.altair_chart(ch, use_container_width=True)

    if ant:
        cai_total = ref["dia_util"] - ant["dia_util"]
        cai_cartao = ref["cartao_du"] - ant["cartao_du"]
        if cai_total < 0:
            parcela = (cai_cartao / cai_total * 100) if cai_total else 0
            cabeca = (f"**Caiu {brl(-cai_total)} por dia útil** "
                      f"({num(var_pct(ref['dia_util'], ant['dia_util']), 1, True)}%) contra {rot_ant}. ")
            if cai_cartao < 0 and parcela >= 100:
                st.error(cabeca + f"**A queda é toda do cartão** — ele caiu {brl(-cai_cartao)} "
                         f"por dia útil, mais que a queda total: PIX e boleto até subiram "
                         f"{brl(-cai_cartao + cai_total)} por dia útil e seguraram parte do tombo.")
            elif cai_cartao < 0:
                st.error(cabeca + f"O cartão responde por **{pct(parcela)}** dessa queda "
                         f"({brl(-cai_cartao)} por dia útil).")
            else:
                st.error(cabeca + "A queda não veio do cartão — veja PIX e boleto no comparativo.")
        else:
            st.success(f"Receita por dia útil **subiu {brl(cai_total)}** contra {rot_ant}.")

    st.markdown("##### 💳 Cartão por adquirente")
    ades = []
    for nome, chave in ADQUIRENTES.items():
        vals = {}
        for m in MESES:
            filtro = f" AND l.tipo='entrada' AND LOWER(l.descricao) LIKE {ASPA}%{chave}%{ASPA}"
            vals[m] = soma(m, filtro)
        if any(vals.values()):
            ades.append({"Adquirente": nome, **{rotulo(m): vals[m] for m in MESES}})
    if ades:
        dfa = pd.DataFrame(ades)
        showa = dfa.copy()
        for m in MESES:
            showa[rotulo(m)] = showa[rotulo(m)].map(brl)
        st.dataframe(showa, use_container_width=True, hide_index=True)
        st.caption("Troca de adquirente aparece aqui: um cai, o outro sobe. Se a soma dos dois "
                   "não recompõe, a queda é de venda — não de migração. A *Liberação "
                   "Vinculada* é cartão antecipado liberado pelo Safra na Matriz: entra no "
                   "total do cartão e também no total antecipado.")

    if ref["antecip"]:
        txt = (f"**Antecipação de recebíveis: {brl(ref['antecip'])} na janela — "
               f"{pct(ref['antecip_pct'])} de tudo que entrou de venda.** ")
        if ant and ref["antecip_pct"] > ant["antecip_pct"] + 1:
            txt += (f"Era {pct(ant['antecip_pct'])} em {rot_ant}: a dependência está "
                    f"**subindo {num(ref['antecip_pct'] - ant['antecip_pct'])} pontos**. ")
        txt += ("Antecipar não cria receita, só muda a data: a venda continua a mesma, "
                "recebida antes e com desconto. Quanto maior a fatia, mais o mês de hoje "
                "está vivendo do recebimento do mês que vem — e o custo do desconto vem "
                "embutido no valor líquido, então **não aparece como despesa em lugar "
                "nenhum desta tela**. Para medir o custo, informe a taxa na aba 🎯.")
        st.warning(txt)

    with st.expander("🔍 Ver detalhes do cálculo"):
        st.markdown(f"""
- **Janela:** dia 01 a {dia_corte:02d} de cada mês — o corte é a conta que exporta mais devagar
  neste mês ({nome_travou}). Nenhum mês entra com mais dias que o outro.
- **Dias úteis:** segunda a sexta (feriado não é descontado — se o mês tem feriado,
  a média sai levemente para baixo nos dois meses comparados).
- **Receita:** entradas classificadas como *Receita de Vendas* ou *Receita de Serviços*
  (plano 1 e 2). Não inclui aluguel, transferência entre empresas nem resgate de aplicação.
- **Cartão:** entradas cujo histórico cita a adquirente ({', '.join(ADQUIRENTES)}).
  Está dentro da receita, não somado a ela.
- **Antecipação:** entradas com "antecipação" no histórico **ou** "Liberação Vinculada"
  (o Safra libera na Matriz o cartão antecipado sem citar a adquirente). É subconjunto
  do cartão, não uma parcela somada à receita.
- Contas fora do comparativo: {len(atrasadas)}.
""")

# ═════════════════════════════════════════════════════════════════════════════
# 2. VENDA × COMPRA
# ═════════════════════════════════════════════════════════════════════════════
with t2:
    st.subheader("A tesoura: o que entra de venda contra o que sai para mercadoria")
    st.caption("⚠️ A coluna de compra é **caixa**: o que efetivamente saiu do banco na janela. "
               "Compra a prazo entra no dia em que o boleto foi pago, não no dia da nota — "
               "então o que pressiona o caixa deste mês foi comprado 30 a 60 dias atrás.")
    linhas = [{"Mês": rotulo(m), "Receita": D[m]["receita"], "Pago de mercadoria": D[m]["custos"],
               "Pagamento ÷ venda": D[m]["custo_pct"],
               "Sobra bruta": D[m]["receita"] - D[m]["custos"]} for m in MESES]
    # O previsto: títulos de MERCADORIA do Argos vencendo na mesma janela. Sem ele
    # não dá para saber se o mês pagou mais porque comprou mais ou porque venceu mais.
    def venc_mercadoria(m: str) -> float:
        sql = ("SELECT COALESCE(SUM(t.valor),0) v FROM titulos t "
               "WHERE LOWER(COALESCE(t.tipo_docto,'')) LIKE '%mercadoria%' "
               "AND substr(t.vencimento,1,7)=? "
               "AND CAST(substr(t.vencimento,9,2) AS INTEGER)<=?")
        par = [m, dia_corte]
        if emp_par:
            sql += " AND t.empresa_id=?"
            par += emp_par
        return float(query(sql, tuple(par))[0]["v"])

    tem_titulo = False
    for i, m in enumerate(MESES):
        v = venc_mercadoria(m)
        linhas[i]["Venceu no Argos"] = v
        linhas[i]["% do que venceu"] = (D[m]["custos"] / v * 100) if v else 0
        tem_titulo = tem_titulo or v > 0

    df = pd.DataFrame(linhas)
    cols = ["Mês", "Receita", "Pago de mercadoria", "Pagamento ÷ venda", "Sobra bruta"]
    if tem_titulo:
        cols = ["Mês", "Receita", "Venceu no Argos", "Pago de mercadoria",
                "% do que venceu", "Pagamento ÷ venda", "Sobra bruta"]
    show = df[cols].copy()
    for col in ("Receita", "Pago de mercadoria", "Sobra bruta"):
        show[col] = show[col].map(brl)
    show["Pagamento ÷ venda"] = show["Pagamento ÷ venda"].map(pct)
    if tem_titulo:
        # Mês sem "A Pagar Geral" importada não é mês que venceu zero: mostra travessão,
        # senão a linha vira "0,0% do que venceu" e parece calote.
        show["Venceu no Argos"] = df["Venceu no Argos"].map(
            lambda v: brl(v) if v else "— (sem A Pagar importada)")
        show["% do que venceu"] = [pct(p) if v else "—"
                                   for p, v in zip(df["% do que venceu"], df["Venceu no Argos"])]
    st.dataframe(show, use_container_width=True, hide_index=True)

    graf = pd.DataFrame([{"Mês": rotulo(m), "Tipo": "Receita", "Valor": D[m]["receita"]} for m in MESES]
                        + [{"Mês": rotulo(m), "Tipo": "Mercadoria", "Valor": D[m]["custos"]} for m in MESES])
    ch = (alt.Chart(graf).mark_bar()
          .encode(x=alt.X("Tipo:N", title=None, axis=None),
                  y=alt.Y("Valor:Q", title="R$ na janela"),
                  color=alt.Color("Tipo:N", scale=alt.Scale(domain=["Receita", "Mercadoria"],
                                                            range=[VERDE, VERMELHO])),
                  column=alt.Column("Mês:N", sort=[rotulo(m) for m in reversed(MESES)], title=None),
                  tooltip=["Mês", "Tipo", alt.Tooltip("Valor:Q", format=",.2f")])
          .properties(height=240, width=110))
    st.altair_chart(ch, use_container_width=False)

    if ant:
        custo_no_ritmo = ref["receita"] * ant["custo_pct"] / 100
        excesso = ref["custos"] - custo_no_ritmo
        venc_ref, venc_ant = df.iloc[0].get("Venceu no Argos", 0), df.iloc[1].get("Venceu no Argos", 0)
        if excesso > 0:
            msg = (f"**Saiu {brl(excesso)} a mais do que sairia no ritmo de {rot_ant}** "
                   f"({pct(ant['custo_pct'])} da venda seriam {brl(custo_no_ritmo)}, e saíram "
                   f"{brl(ref['custos'])}) — cerca de {brl(excesso / dia_corte * 30)} no mês inteiro. ")
            if tem_titulo and venc_ant:
                dif_venc = (venc_ref - venc_ant) / venc_ant * 100
                if dif_venc > 5:
                    msg += (f"**Mas não é descontrole de pagamento:** venceu {num(dif_venc)}% mais "
                            f"de mercadoria no Argos ({brl(venc_ref)} contra {brl(venc_ant)}) e você "
                            f"pagou praticamente a mesma proporção do que venceu "
                            f"({pct(df.iloc[0]['% do que venceu'])} contra "
                            f"{pct(df.iloc[1]['% do que venceu'])}). O aperto veio da **compra feita "
                            f"30 a 60 dias atrás**, que está vencendo agora com a venda menor. "
                            f"Cortar compra hoje só alivia o caixa daqui a um a dois meses — por isso "
                            f"a decisão é urgente mesmo sem efeito imediato.")
                else:
                    msg += ("O que venceu no Argos ficou praticamente igual ao mês anterior, "
                            "então a diferença é de pagamento, não de vencimento.")
            else:
                msg += ("Sem os títulos do Argos importados nesta janela não dá para saber se "
                        "venceu mais ou se pagou mais — importe a *A Pagar Geral* do período "
                        "para separar as duas coisas.")
            st.error(msg)
        else:
            st.success(f"O pagamento de mercadoria está proporcionalmente **abaixo** de {rot_ant}: "
                       f"{brl(-excesso)} a menos na janela.")

    st.markdown("##### 🏭 Maiores compras da janela")
    top = query(
        """SELECT COALESCE(NULLIF(l.contraparte,''), l.descricao) AS "Fornecedor",
                  COUNT(*) AS "Qtd", SUM(l.valor) AS "Valor"
           FROM lancamentos l JOIN plano_contas p ON p.id=l.plano_conta_id
           WHERE l.tipo='saida' AND p.grupo='Custos' AND substr(l.data,1,7)=?
             AND CAST(substr(l.data,9,2) AS INTEGER)<=?""" + emp_sql +
        """ GROUP BY 1 ORDER BY 3 DESC LIMIT 15""",
        tuple([mes, dia_corte] + emp_par))
    if top:
        dft = pd.DataFrame(top)
        dft["% da compra"] = dft["Valor"] / max(ref["custos"], 1) * 100
        dft["Valor"] = dft["Valor"].map(brl)
        dft["% da compra"] = dft["% da compra"].map(pct)
        st.dataframe(dft, use_container_width=True, hide_index=True)

    with st.expander("🔍 Ver detalhes do cálculo"):
        st.markdown("""
- **Pago de mercadoria:** saídas do grupo *Custos* — CMV, material/insumo, frete sobre
  compras, carcaças e custo de serviço prestado. É **regime de caixa**: o dinheiro que
  saiu do banco na janela, não a nota fiscal emitida. Compra a prazo aparece no dia do
  pagamento, então este número reflete decisão de compra de 30 a 60 dias atrás.
- **Venceu no Argos:** títulos com tipo *MERCADORIA* cujo vencimento cai na mesma janela.
  É o previsto contra o realizado — é ele que separa "pagou mais" de "comprou mais".
  Só aparece se a *A Pagar Geral* do período já tiver sido importada.
- **% do que venceu:** pago ÷ vencido. Estável entre meses = pagamento sob controle,
  o que mudou foi o volume que venceu.
- **Pagamento ÷ venda:** quanto da venda da janela foi embora em mercadoria.
- A projeção para o mês inteiro é regra de três sobre os dias corridos da janela —
  ordem de grandeza, não fechamento.
""")

# ═════════════════════════════════════════════════════════════════════════════
# 3. GERAÇÃO DE CAIXA
# ═════════════════════════════════════════════════════════════════════════════
with t3:
    st.subheader("Da venda até o que sobra para pagar banco")
    ordem = [("Receita de vendas", "receita", 1), ("Receita de aluguel", "aluguel", 1),
             ("(−) Compras", "custos", -1), ("(−) Pessoal", "pessoal", -1),
             ("(−) Estrutura (admin, ocupação, comercial)", "estrutura", -1),
             ("(−) Tributos e deduções", "tributos", -1),
             ("(−) Tarifas, juros e IOF", "financeiras", -1)]
    linhas = [{"Linha": nome, **{rotulo(m): D[m][k] * s for m in MESES}} for nome, k, s in ordem]
    linhas.append({"Linha": "= GERAÇÃO OPERACIONAL", **{rotulo(m): D[m]["geracao"] for m in MESES}})
    linhas.append({"Linha": "(−) Sócios (pró-labore + pessoais)",
                   **{rotulo(m): -D[m]["socios"] for m in MESES}})
    linhas.append({"Linha": "= DEPOIS DOS SÓCIOS", **{rotulo(m): D[m]["apos_socios"] for m in MESES}})
    linhas.append({"Linha": "(−) Parcelas de empréstimo", **{rotulo(m): -D[m]["divida"] for m in MESES}})
    linhas.append({"Linha": "= SOBRA / FALTA", **{rotulo(m): D[m]["sobra"] for m in MESES}})
    df = pd.DataFrame(linhas)
    show = df.copy()
    for m in MESES:
        show[rotulo(m)] = show[rotulo(m)].map(brl)
    st.dataframe(show, use_container_width=True, hide_index=True)

    if ref["sobra"] < 0:
        st.error(f"**Nesta janela a operação não pagou a própria dívida: faltaram "
                 f"{brl(-ref['sobra'])}.** A diferença saiu do caixa, de aplicação resgatada "
                 f"ou de crédito novo.")
    else:
        st.success(f"**Sobraram {brl(ref['sobra'])} na janela** depois de pagar tudo, inclusive "
                   "sócios e parcela de banco.")

    st.caption("Movimentação interna (transferência entre empresas, aplicação/resgate, "
               "suprimento de caixa) fica de fora: não é ganho nem gasto, só dinheiro "
               "trocando de bolso.")

    with st.expander("🔍 Ver detalhes do cálculo"):
        st.markdown("""
- Cada linha é a soma das saídas do **grupo** correspondente do plano de contas, na
  janela justa. Nada de bucket "outros": o que não tem categoria aparece em Pendências.
- **Sócios** junta pró-labore (grupo *Sócios*) e *Gastos Pessoais (Sócios)* — os dois
  saem do caixa da empresa, independentemente de entrarem na DRE.
- **Parcelas de empréstimo** = plano *Empréstimos/Financiamentos*. Só o que JÁ debitou
  dentro da janela; o que ainda vai debitar no mês está na aba 🏦 Dívida.
- **Sobra/falta** é caixa, não lucro: compra de estoque pesa aqui no dia do pagamento.
""")

# ═════════════════════════════════════════════════════════════════════════════
# 4. DÍVIDA
# ═════════════════════════════════════════════════════════════════════════════
PARC_RE = re.compile(r"(\d{1,3})\s*/\s*(\d{1,3})")
NUM_RE = re.compile(r"\d{6,}")


def chave_contrato(desc: str) -> str:
    """Assinatura estável do contrato: o histórico do banco varia ('Parc 004/042',
    'PARC 005/042', 'Debito Emprestimo'), então tira número de parcela e de
    contrato e fica o texto-base — que, junto com a conta, identifica o contrato."""
    t = (desc or "").lower()
    t = PARC_RE.sub(" ", t)
    t = NUM_RE.sub(" ", t)
    t = re.sub(r"\bparc(ela)?\b", " ", t)
    t = re.sub(r"[^a-zà-ú ]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


with t4:
    st.subheader("O que o banco está levando por mês")

    m1, m2, m3 = st.columns(3)
    m1.metric(f"Parcelas pagas na janela ({rotulo(mes)})", brl(ref["divida"]))
    m2.metric(f"Mesma janela em {rot_ant}", brl(ant["divida"]) if ant else "—")
    m3.metric("Tarifas, juros e IOF na janela", brl(ref["financeiras"]))

    # Cada débito de empréstimo já registrado, agrupado por contrato e por mês.
    deb = query(
        """SELECT l.data, l.descricao, l.valor, l.conta_bancaria_id c,
                  COALESCE(cb.descricao, cb.banco) banco, e.apelido empresa
           FROM lancamentos l
           LEFT JOIN contas_bancarias cb ON cb.id=l.conta_bancaria_id
           LEFT JOIN empresas e ON e.id=l.empresa_id
           WHERE l.tipo='saida' AND l.plano_conta_id=39""" + emp_sql +
        " ORDER BY l.data", tuple(emp_par))

    contratos = defaultdict(lambda: {"meses": defaultdict(float), "dias": [], "parcelas": [],
                                     "banco": "", "empresa": "", "desc": ""})
    for r in deb:
        k = (r["c"], chave_contrato(r["descricao"]))
        c = contratos[k]
        c["meses"][r["data"][:7]] += float(r["valor"])
        c["dias"].append(int(r["data"][8:10]))
        c["banco"] = r["banco"] or "—"
        c["empresa"] = r["empresa"] or "—"
        c["desc"] = c["desc"] or (r["descricao"] or "")
        mp = PARC_RE.search(r["descricao"] or "")
        if mp:
            c["parcelas"].append((r["data"], int(mp.group(1)), int(mp.group(2))))

    if not contratos:
        st.info("Nenhum débito de empréstimo classificado no plano *Empréstimos/Financiamentos*.")
    else:
        st.markdown("##### 📄 Contratos que o extrato revela")
        st.caption("Reconstruído pelos débitos: o banco desconta direto na conta, não passa "
                   "pelo Argos. O número de parcelas só aparece quando o histórico traz "
                   "'Parc 004/042'.")
        linhas, previstos = [], []
        for (conta, chave), c in sorted(contratos.items(),
                                        key=lambda kv: -sum(kv[1]["meses"].values())):
            mensal = sorted(c["meses"].items())
            ultimo_mes, ultimo_valor = mensal[-1]
            tipico = sorted(v for _, v in mensal)[len(mensal) // 2]
            dia_tipico = max(set(c["dias"]), key=c["dias"].count)
            restante_txt, falta_valor = "—", None
            if c["parcelas"]:
                dt, n, total = max(c["parcelas"])
                faltam = max(total - n, 0)
                falta_valor = faltam * tipico
                restante_txt = f"parcela {n} de {total} · faltam {faltam} ≈ {brl(falta_valor)}"
            linhas.append({
                "Contrato (histórico do banco)": (c["desc"] or chave)[:46],
                "Empresa": c["empresa"], "Banco": c["banco"],
                "Parcela típica": tipico, "Dia do débito": dia_tipico,
                "Último débito": ultimo_mes, "Situação": restante_txt})
            # Ainda vai debitar neste mês? Só entra contrato com recorrência
            # comprovada (debitou em 2+ meses) — pagamento avulso de empréstimo
            # não volta no mês seguinte e viraria previsão falsa.
            if len(mensal) >= 2 and mes not in c["meses"] and dia_tipico > dia_corte:
                previstos.append((c["desc"] or chave, c["banco"], dia_tipico, tipico))
        dfc = pd.DataFrame(linhas)
        dfc["Parcela típica"] = dfc["Parcela típica"].map(brl)
        st.dataframe(dfc, use_container_width=True, hide_index=True)

        if previstos:
            total_prev = sum(p[3] for p in previstos)
            st.warning(
                f"**Ainda deve debitar neste mês: {brl(total_prev)}** — "
                + " · ".join(f"{d[:28]} ({b}) ~dia {dia}: {brl(v)}"
                             for d, b, dia, v in sorted(previstos, key=lambda x: x[2]))
                + f". Somando ao que já saiu, {rotulo(mes)} fecha em ~"
                  f"{brl(ref['divida'] + total_prev)} de parcela.")
        else:
            st.success("Nenhuma parcela recorrente conhecida ficou para debitar depois do "
                       f"dia {dia_corte:02d} — o mês já mostrou o que o banco leva.")

    st.divider()
    st.markdown("### 📝 Contratos cadastrados")
    st.caption("O extrato mostra o que já saiu; **o contrato mostra o que vem**. "
               "Com carência (meses só de juros), a parcela de hoje é pequena e sobe "
               "de degrau quando começa a amortizar — é isso que a projeção abaixo antecipa.")

    contas_lista = query(
        """SELECT cb.id, cb.banco, cb.descricao, cb.empresa_id, e.apelido
           FROM contas_bancarias cb JOIN empresas e ON e.id=cb.empresa_id ORDER BY cb.id""")
    rot_conta = {c["id"]: f"{c['apelido']} / {c['banco']}" for c in contas_lista}
    rot_emp = {e["id"]: e["apelido"] for e in empresas}

    cad = query("SELECT * FROM emprestimos_contratos ORDER BY situacao, banco, numero")
    df_cad = pd.DataFrame(cad) if cad else pd.DataFrame(columns=[
        "id", "empresa_id", "conta_bancaria_id", "banco", "numero", "apelido",
        "valor_contratado", "data_contratacao", "taxa_am", "prazo_meses", "carencia_meses",
        "parcela_carencia", "parcela_apos", "dia_debito", "saldo_devedor", "saldo_em",
        "situacao", "observacao", "criado_em"])
    edit_cols = ["id", "apelido", "empresa_id", "banco", "numero", "valor_contratado",
                 "data_contratacao", "taxa_am", "carencia_meses", "parcela_carencia",
                 "prazo_meses", "parcela_apos", "dia_debito", "saldo_devedor", "saldo_em",
                 "situacao", "observacao"]
    df_edit = df_cad.reindex(columns=edit_cols)

    editado = st.data_editor(
        df_edit, use_container_width=True, hide_index=True, num_rows="dynamic",
        key="editor_contratos",
        column_config={
            "id": st.column_config.NumberColumn("id", disabled=True, width="small"),
            "apelido": st.column_config.TextColumn("Como você chama", width="medium"),
            "empresa_id": st.column_config.SelectboxColumn(
                "Empresa", options=[e["id"] for e in empresas], required=False),
            "banco": st.column_config.TextColumn("Banco"),
            "numero": st.column_config.TextColumn("Nº do contrato"),
            "valor_contratado": st.column_config.NumberColumn("Valor tomado", format="%.2f"),
            "data_contratacao": st.column_config.TextColumn("Contratado em (AAAA-MM-DD)"),
            "taxa_am": st.column_config.NumberColumn("Taxa % a.m.", format="%.2f"),
            "carencia_meses": st.column_config.NumberColumn("Carência (meses só juros)"),
            "parcela_carencia": st.column_config.NumberColumn("Parcela na carência", format="%.2f"),
            "prazo_meses": st.column_config.NumberColumn("Parcelas de amortização"),
            "parcela_apos": st.column_config.NumberColumn("Parcela depois (0 = calcular)",
                                                          format="%.2f"),
            "dia_debito": st.column_config.NumberColumn("Dia do débito"),
            "saldo_devedor": st.column_config.NumberColumn("Saldo devedor", format="%.2f"),
            "saldo_em": st.column_config.TextColumn("Saldo na data (AAAA-MM-DD)"),
            "situacao": st.column_config.SelectboxColumn("Situação",
                                                         options=["ativo", "quitado"]),
            "observacao": st.column_config.TextColumn("Observação", width="medium"),
        })
    st.caption("Empresa: " + " · ".join(f"{i}={n}" for i, n in rot_emp.items()))

    if st.button("💾 Salvar contratos", type="primary"):
        campos = [c for c in edit_cols if c != "id"]
        ids_antes = set(int(i) for i in df_cad["id"]) if not df_cad.empty else set()
        ids_depois = set()
        novos = alterados = 0
        for _, linha in editado.iterrows():
            vals = []
            for c in campos:
                v = linha.get(c)
                if pd.isna(v):
                    v = None
                elif isinstance(v, (pd.Timestamp, date)):
                    v = str(v)[:10]
                elif hasattr(v, "item"):
                    v = v.item()
                vals.append(v)
            rid = linha.get("id")
            if pd.isna(rid):
                if not any(v not in (None, "", 0) for v in vals):
                    continue
                execute(f"INSERT INTO emprestimos_contratos ({','.join(campos)}) "
                        f"VALUES ({','.join('?' for _ in campos)})", tuple(vals))
                novos += 1
            else:
                rid = int(rid)
                ids_depois.add(rid)
                execute("UPDATE emprestimos_contratos SET "
                        + ", ".join(f"{c}=?" for c in campos) + " WHERE id=?",
                        tuple(vals + [rid]))
                alterados += 1
        for rid in ids_antes - ids_depois:
            execute("DELETE FROM emprestimos_contratos WHERE id=?", (rid,))
        st.success(f"Salvo: {novos} novo(s), {alterados} atualizado(s), "
                   f"{len(ids_antes - ids_depois)} removido(s).")
        st.rerun()

    # ── Projeção do degrau da carência ───────────────────────────────────────
    ativos = [c for c in cad if (c.get("situacao") or "ativo") == "ativo"]
    if ativos:
        st.markdown("##### 📈 Quanto o banco vai levar nos próximos 12 meses")

        def parcela_do_mes(ct: dict, alvo: str) -> float:
            """Quanto esse contrato debita no mês 'AAAA-MM'."""
            dc = (ct.get("data_contratacao") or "")[:7]
            if len(dc) < 7:
                return 0.0
            n = ((int(alvo[:4]) * 12 + int(alvo[5:7]))
                 - (int(dc[:4]) * 12 + int(dc[5:7])))
            if n < 0:
                return 0.0
            carencia = int(ct.get("carencia_meses") or 0)
            prazo = int(ct.get("prazo_meses") or 0)
            taxa = float(ct.get("taxa_am") or 0) / 100
            valor = float(ct.get("valor_contratado") or 0)
            if n < carencia:
                pc = float(ct.get("parcela_carencia") or 0)
                return pc if pc else valor * taxa
            if prazo and n < carencia + prazo:
                pa = float(ct.get("parcela_apos") or 0)
                if pa:
                    return pa
                if taxa and valor:
                    return valor * taxa / (1 - (1 + taxa) ** (-prazo))
                return valor / prazo if prazo else 0.0
            return 0.0

        proj, hoje = [], mes
        for i in range(12):
            y, mm = int(hoje[:4]), int(hoje[5:7]) + i
            alvo = f"{y + (mm - 1) // 12:04d}-{(mm - 1) % 12 + 1:02d}"
            total = sum(parcela_do_mes(c, alvo) for c in ativos)
            proj.append({"Mês": rotulo(alvo), "mes_iso": alvo, "Parcela do mês": total})
        dfp = pd.DataFrame(proj)
        ch = (alt.Chart(dfp).mark_bar(color=VERMELHO)
              .encode(x=alt.X("Mês:N", sort=list(dfp["Mês"]), title=None),
                      y=alt.Y("Parcela do mês:Q", title="R$ por mês"),
                      tooltip=["Mês", alt.Tooltip("Parcela do mês:Q", format=",.2f")])
              .properties(height=260))
        st.altair_chart(ch, use_container_width=True)
        hoje_v, pico = dfp.iloc[0]["Parcela do mês"], dfp["Parcela do mês"].max()
        if pico > hoje_v * 1.05:
            mes_pico = dfp.loc[dfp["Parcela do mês"].idxmax(), "Mês"]
            st.error(f"**Degrau à vista:** hoje a parcela soma {brl(hoje_v)}/mês e chega a "
                     f"**{brl(pico)}/mês em {mes_pico}** — {brl(pico - hoje_v)} a mais, quando "
                     "os contratos saem da carência e começam a amortizar. É esse número que "
                     "precisa caber na geração de caixa, não o de hoje.")
        st.dataframe(dfp[["Mês", "Parcela do mês"]].assign(
            **{"Parcela do mês": dfp["Parcela do mês"].map(brl)}),
            use_container_width=True, hide_index=True)
    else:
        st.info("Cadastre os contratos acima (com **carência** e **parcela depois da carência**) "
                "para a tela projetar o degrau dos próximos 12 meses. Enquanto não houver "
                "contrato cadastrado, só dá para ver o que já debitou.")

    with st.expander("🔍 Ver detalhes do cálculo"):
        st.markdown("""
- **Contratos que o extrato revela:** agrupa os débitos do plano *Empréstimos/Financiamentos*
  por conta + histórico normalizado (tira o "Parc 004/042" e o número do contrato, que mudam
  todo mês). A **parcela típica** é a mediana do total debitado por mês — mediana, e não
  média, porque um mês com quitação antecipada distorceria a média.
- **"Faltam N parcelas"** só aparece quando o banco escreve a parcela no histórico
  (ex.: `PARC 022/030`). O valor é uma estimativa: nº de parcelas restantes × parcela típica,
  **sem** considerar juros futuros nem saldo devedor real.
- **"Ainda deve debitar neste mês":** contrato que costuma debitar num dia posterior ao
  corte da janela e ainda não apareceu neste mês.
- **Projeção de 12 meses:** usa o contrato cadastrado. Durante a carência considera
  `parcela na carência` (ou, se em branco, valor × taxa a.m.). Depois usa `parcela depois`;
  se estiver 0, calcula a prestação pela fórmula Price sobre o valor tomado.
""")

# ═════════════════════════════════════════════════════════════════════════════
# 5. PLANO DE SAÍDA
# ═════════════════════════════════════════════════════════════════════════════
with t5:
    st.subheader("Quanto sobraria para quitar — e em quanto tempo")
    st.caption("Os três cortes que dependem só de decisão interna, aplicados sobre o mês "
               "de referência. Mexa nos controles e veja a sobra mudar.")

    fator_mes = 30 / max(dia_corte, 1)      # da janela para o mês inteiro
    base_compra = ref["custos"] * fator_mes
    base_socios = ref["socios"] * fator_mes
    base_ger = ref["geracao"] * fator_mes
    base_div = ref["divida"] * fator_mes
    base_antecip = ref["antecip"] * fator_mes

    c1, c2, c3 = st.columns(3)
    corte_compra = c1.slider("Cortar da compra mensal (%)", 0, 40, 15, 1,
                             help=f"Base: {brl(base_compra)}/mês na proporção da janela")
    corte_socios = c2.slider("Cortar dos sócios (%)", 0, 60, 20, 5,
                             help=f"Base: {brl(base_socios)}/mês (pró-labore + pessoais)")
    custo_antecip = c3.slider("Custo da antecipação (% a.m.)", 0.0, 4.0, 2.0, 0.1,
                              help=f"Taxa cobrada para adiantar o recebível. Base: "
                                   f"{brl(base_antecip)}/mês antecipados "
                                   f"({pct(ref['antecip_pct'])} da receita)")

    ganho_compra = base_compra * corte_compra / 100
    ganho_socios = base_socios * corte_socios / 100
    ganho_antecip = base_antecip * custo_antecip / 100
    sobra_hoje = base_ger - base_socios - base_div
    sobra_nova = sobra_hoje + ganho_compra + ganho_socios + ganho_antecip

    a, b, c = st.columns(3)
    a.metric("Sobra hoje (mês projetado)", brl(sobra_hoje))
    b.metric("Ganho com os cortes", brl(ganho_compra + ganho_socios + ganho_antecip))
    c.metric("Sobra depois dos cortes", brl(sobra_nova),
             f"{brl(sobra_nova - sobra_hoje)}")

    st.markdown(f"""
| Alavanca | Base mensal | Corte | Libera por mês |
|---|---:|---:|---:|
| Compra de mercadoria | {brl(base_compra)} | {corte_compra}% | **{brl(ganho_compra)}** |
| Retirada dos sócios | {brl(base_socios)} | {corte_socios}% | **{brl(ganho_socios)}** |
| Parar de antecipar recebível | {brl(base_antecip)} antecipados | {num(custo_antecip)}% a.m. | **{brl(ganho_antecip)}** |
""")

    contratos_cad = [c for c in query(
        "SELECT * FROM emprestimos_contratos WHERE COALESCE(situacao,'ativo')='ativo'")
        if (c.get("saldo_devedor") or 0) > 0]
    if contratos_cad and sobra_nova > 0:
        st.markdown("##### 🎯 Ordem de quitação (mais caro primeiro)")
        st.caption("Método avalanche: quitar o de maior taxa economiza mais juros do que "
                   "quitar o de menor saldo. A linha subsidiada (PEAC/FGI) costuma ser a "
                   "mais barata — deve ser a última.")
        caixa, linhas, acumulado = sobra_nova, [], 0
        for ordem_i, ct in enumerate(
                sorted(contratos_cad, key=lambda x: -(x.get("taxa_am") or 0)), start=1):
            saldo = float(ct.get("saldo_devedor") or 0)
            acumulado += saldo
            # O prazo é CUMULATIVO: o 2º contrato só começa a ser quitado depois
            # do 1º, então o que vale é o acumulado dividido pela sobra mensal.
            meses = acumulado / caixa if caixa > 0 else None
            linhas.append({
                "Ordem": ordem_i,
                "Contrato": ct.get("apelido") or ct.get("numero") or ct.get("banco") or "—",
                "Banco": ct.get("banco") or "—",
                "Taxa % a.m.": ct.get("taxa_am"),
                "Saldo devedor": brl(saldo),
                "Quitado no mês nº": "—" if not meses else num(meses),
                "Acumulado": brl(acumulado)})
        st.dataframe(pd.DataFrame(linhas), use_container_width=True, hide_index=True)
        total_divida = sum(float(c.get("saldo_devedor") or 0) for c in contratos_cad)
        prazo_txt = num(total_divida / sobra_nova)
        st.info(f"**Dívida total cadastrada: {brl(total_divida)}.** Com a sobra de "
                f"{brl(sobra_nova)}/mês, a saída completa leva **{prazo_txt} meses** — "
                "sem considerar os juros que continuam correndo sobre o saldo, então trate "
                "como piso, não como prazo.")
    elif not contratos_cad:
        st.info("Preencha o **saldo devedor** e a **taxa** de cada contrato na aba 🏦 Dívida "
                "para a tela montar a ordem de quitação e o prazo de saída.")
    elif sobra_nova <= 0:
        st.error("Com os cortes atuais a operação ainda não gera sobra — não há o que amortizar. "
                 "Aumente o corte da compra: é a alavanca de maior peso.")

    with st.expander("🔍 Ver detalhes do cálculo"):
        st.markdown(f"""
- As bases mensais vêm da janela ({dia_corte} dias corridos) multiplicadas por
  {num(fator_mes, 2)} para virar mês cheio — ordem de grandeza, não fechamento.
- **Sobra hoje** = geração operacional − sócios − parcelas, tudo projetado para o mês.
- **Corte na compra** assume que a venda não cai junto: vale enquanto houver estoque para
  girar. Por isso o teto do controle é 40% e não 100%.
- **Antecipação** economiza só a taxa (o principal você recebe de qualquer forma, alguns
  dias depois). O ganho mostrado é `valor antecipado × taxa`, por mês.
- **Meses para quitar** = saldo ÷ sobra mensal, sem juros no meio do caminho. Serve para
  ordenar decisões, não para negociar prazo com o banco.
""")
