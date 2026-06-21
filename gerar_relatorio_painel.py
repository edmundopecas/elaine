"""
Gera um PDF executivo da aba "Painel de Saídas" para a diretoria.
Espelha views/apresentacao.py: separa despesa real x movimento interno x a classificar.

Uso:
    python gerar_relatorio_painel.py                 # grupo todo, período completo
    python gerar_relatorio_painel.py "Edmundo Matriz"  # uma empresa
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import date, datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle, HRFlowable)
from reportlab.graphics.shapes import Drawing, String
from reportlab.graphics.charts.piecharts import Pie
from reportlab.graphics.charts.barcharts import HorizontalBarChart

VERDE = colors.HexColor("#2e7d32")
CINZA = colors.HexColor("#90a4ae")
AMBAR = colors.HexColor("#f9a825")
AZUL = colors.HexColor("#1565c0")
ESCURO = colors.HexColor("#1b1b1b")


def brl(v: float) -> str:
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def main():
    sel_emp = sys.argv[1] if len(sys.argv) > 1 else "Todas"

    con = sqlite3.connect("elaine.db")
    con.row_factory = sqlite3.Row
    q = con.execute

    empresas = q("SELECT id, apelido FROM empresas WHERE ativa=1 ORDER BY apelido").fetchall()
    ext = q("SELECT MIN(data) lo, MAX(data) hi FROM lancamentos WHERE tipo='saida'").fetchone()
    d_ini = ext["lo"]
    d_fim = ext["hi"]

    cond = ["l.tipo='saida'", "l.data BETWEEN ? AND ?"]
    params = [d_ini, d_fim]
    if sel_emp != "Todas":
        eid = next(e["id"] for e in empresas if e["apelido"] == sel_emp)
        cond.append("l.empresa_id=?")
        params.append(eid)
    where = " AND ".join(cond)

    rows = q(
        f"""SELECT COALESCE(p.nome,'A classificar') AS cat, p.entra_dre AS dre,
            COUNT(*) AS qtd, SUM(l.valor) AS total
            FROM lancamentos l LEFT JOIN plano_contas p ON p.id=l.plano_conta_id
            WHERE {where} GROUP BY l.plano_conta_id ORDER BY total DESC""",
        tuple(params),
    ).fetchall()

    real = [r for r in rows if r["dre"] == 1]
    interno = [r for r in rows if r["dre"] == 0]
    pend = [r for r in rows if r["dre"] is None]
    tot_saiu = sum(r["total"] for r in rows)
    tot_real = sum(r["total"] for r in real)
    tot_int = sum(r["total"] for r in interno)
    tot_pend = sum(r["total"] for r in pend)
    n_pend = sum(r["qtd"] for r in pend)
    cmv = sum(r["total"] for r in rows if "Mercadoria" in r["cat"])

    erows = q(
        f"""SELECT e.apelido AS emp, SUM(l.valor) AS total
            FROM lancamentos l JOIN plano_contas p ON p.id=l.plano_conta_id
            JOIN empresas e ON e.id=l.empresa_id
            WHERE {where} AND p.entra_dre=1
            GROUP BY e.apelido ORDER BY total DESC""",
        tuple(params),
    ).fetchall()

    # ── Estilos ───────────────────────────────────────────────────────────────
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Title"], fontSize=20, spaceAfter=2,
                        textColor=ESCURO)
    sub = ParagraphStyle("sub", parent=styles["Normal"], fontSize=10.5,
                         textColor=colors.HexColor("#555555"), spaceAfter=2)
    sec = ParagraphStyle("sec", parent=styles["Heading2"], fontSize=13,
                         textColor=ESCURO, spaceBefore=10, spaceAfter=6)
    nota = ParagraphStyle("nota", parent=styles["Normal"], fontSize=8.5,
                          textColor=colors.HexColor("#666666"), leading=12)

    titulo_grupo = "Grupo Edmundo" if sel_emp == "Todas" else sel_emp
    dt = lambda s: datetime.fromisoformat(s).strftime("%d/%m/%Y")

    elems = []
    elems.append(Paragraph("Painel de Saídas", h1))
    elems.append(Paragraph(f"<b>{titulo_grupo}</b> &nbsp;·&nbsp; "
                           f"{dt(d_ini)} a {dt(d_fim)}", sub))
    elems.append(Paragraph(f"Relatório gerado em {date.today().strftime('%d/%m/%Y')}", sub))
    elems.append(Spacer(1, 4))
    elems.append(HRFlowable(width="100%", thickness=1.2, color=VERDE))
    elems.append(Spacer(1, 10))

    # ── KPIs (4 cards) ──────────────────────────────────────────────────────────
    def card(titulo, valor, rodape, cor):
        inner = [
            [Paragraph(f"<font size=8 color='#666666'>{titulo}</font>", styles["Normal"])],
            [Paragraph(f"<font size=14 color='#{cor.hexval()[2:]}'><b>{valor}</b></font>",
                       styles["Normal"])],
            [Paragraph(f"<font size=7.5 color='#888888'>{rodape}</font>", styles["Normal"])],
        ]
        t = Table(inner, colWidths=[40 * mm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f5f5f5")),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#e0e0e0")),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        return t

    pct_cmv = f"{(cmv / tot_real * 100):.0f}% das despesas" if tot_real else ""
    kpis = Table([[
        card("DESPESAS REAIS", brl(tot_real), "entra no resultado (DRE)", VERDE),
        card("COMPRA DE MERCADORIA", brl(cmv), pct_cmv, VERDE),
        card("MOVIMENTO INTERNO", brl(tot_int), "transferências/aplicações", CINZA),
        card("A CLASSIFICAR", brl(tot_pend), f"{n_pend} lançamentos", AMBAR),
    ]], colWidths=[44 * mm] * 4)
    kpis.setStyle(TableStyle([("ALIGN", (0, 0), (-1, -1), "CENTER")]))
    elems.append(kpis)
    elems.append(Spacer(1, 12))

    # ── Donut composição ─────────────────────────────────────────────────────────
    elems.append(Paragraph("Composição das saídas", sec))
    comp = [("Despesas reais", tot_real, VERDE),
            ("Movimento interno", tot_int, CINZA),
            ("A classificar", tot_pend, AMBAR)]
    comp = [c for c in comp if c[1] > 0]

    d = Drawing(460, 170)
    pie = Pie()
    pie.x, pie.y = 20, 15
    pie.width = pie.height = 140
    pie.data = [c[1] for c in comp]
    pie.innerRadiusFraction = 0.55
    for i, c in enumerate(comp):
        pie.slices[i].fillColor = c[2]
        pie.slices[i].strokeColor = colors.white
        pie.slices[i].strokeWidth = 1.5
    d.add(pie)
    # legenda manual à direita
    ly = 130
    for nome, val, cor in comp:
        d.add(String(190, ly, "■", fillColor=cor, fontSize=12))
        pct = val / tot_saiu * 100 if tot_saiu else 0
        d.add(String(205, ly, f"{nome}: {brl(val)} ({pct:.0f}%)",
                     fillColor=ESCURO, fontSize=9.5))
        ly -= 22
    elems.append(d)
    if tot_saiu:
        elems.append(Paragraph(
            f"De cada R$ 1,00 que saiu do caixa, <b>{brl(tot_real / tot_saiu).replace('R$ ','R$ ')}</b> "
            f"foi despesa real — o restante apenas circulou entre contas ou ainda está em classificação.",
            nota))
    elems.append(Spacer(1, 10))

    # ── Para onde foi o dinheiro (despesas reais) ────────────────────────────────
    def barras_horizontais(dados, cor, largura=460, max_barras=None):
        """dados = lista de (label, valor) já ordenada desc."""
        if max_barras:
            dados = dados[:max_barras]
        n = len(dados)
        altura = 24 + 20 * n
        dr = Drawing(largura, altura)
        bc = HorizontalBarChart()
        bc.x = 130
        bc.y = 10
        bc.width = largura - 230
        bc.height = altura - 20
        bc.data = [[d[1] for d in dados][::-1]]  # reverse: maior no topo
        labels = [d[0] for d in dados][::-1]
        bc.categoryAxis.categoryNames = labels
        bc.categoryAxis.labels.fontSize = 8
        bc.categoryAxis.labels.dx = -4
        bc.categoryAxis.labels.boxAnchor = "e"
        bc.bars[0].fillColor = cor
        bc.bars[0].strokeColor = None
        bc.valueAxis.visible = False
        bc.valueAxis.valueMin = 0
        maxv = max(d[1] for d in dados) if dados else 1
        bc.valueAxis.valueMax = maxv * 1.18
        bc.barWidth = 11
        dr.add(bc)
        # rótulos de valor no fim de cada barra
        plot_w = bc.width
        for i, (lab, val) in enumerate(dados[::-1]):
            xr = bc.x + (val / (maxv * 1.18)) * plot_w + 3
            yr = bc.y + (i + 0.5) * (bc.height / n) - 3
            dr.add(String(xr, yr, brl(val), fillColor=ESCURO, fontSize=7.5))
        return dr

    elems.append(Paragraph("Para onde foi o dinheiro <i>(despesas reais)</i>", sec))
    if real:
        elems.append(barras_horizontais([(r["cat"], r["total"]) for r in real], VERDE))
    elems.append(Spacer(1, 8))

    # ── Despesas reais por empresa ────────────────────────────────────────────────
    if sel_emp == "Todas" and erows:
        elems.append(Paragraph("Despesas reais por empresa", sec))
        elems.append(barras_horizontais([(r["emp"], r["total"]) for r in erows], AZUL))
        elems.append(Spacer(1, 8))

    # ── Tabela detalhada de despesas reais ────────────────────────────────────────
    elems.append(Paragraph("Detalhamento das despesas reais", sec))
    data = [["Categoria", "Qtd", "Total"]]
    for r in real:
        data.append([r["cat"], str(r["qtd"]), brl(r["total"])])
    data.append(["TOTAL DESPESAS REAIS", str(sum(r["qtd"] for r in real)), brl(tot_real)])
    tbl = Table(data, colWidths=[95 * mm, 20 * mm, 45 * mm])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), VERDE),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, colors.HexColor("#f3f7f3")]),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor("#dddddd")),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#e8f0e8")),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    elems.append(tbl)
    elems.append(Spacer(1, 14))

    # ── Rodapé honesto ────────────────────────────────────────────────────────────
    elems.append(HRFlowable(width="100%", thickness=0.6, color=colors.HexColor("#cccccc")))
    elems.append(Spacer(1, 4))
    if tot_pend > 0:
        elems.append(Paragraph(
            f"<b>Nota:</b> ainda há <b>{brl(tot_pend)}</b> em {n_pend} lançamentos a classificar — "
            "os números de despesa devem subir um pouco conforme o refino termina.", nota))
    elems.append(Paragraph(
        "Movimento interno (transferências entre contas e aplicações/resgates) é excluído "
        "do resultado por não representar gasto — apenas circulação de caixa.", nota))

    saida = f"Painel_Saidas_{titulo_grupo.replace(' ', '_')}_{date.today().isoformat()}.pdf"
    doc = SimpleDocTemplate(saida, pagesize=A4,
                            leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=14 * mm, bottomMargin=14 * mm,
                            title=f"Painel de Saídas — {titulo_grupo}")
    doc.build(elems)
    print("PDF gerado:", saida)
    print(f"Despesas reais: {brl(tot_real)} | Interno: {brl(tot_int)} | A classificar: {brl(tot_pend)}")


if __name__ == "__main__":
    main()
