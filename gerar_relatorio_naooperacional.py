"""
Gera um PDF com os GASTOS NÃO-OPERACIONAIS: pagamentos que saíram do caixa do
grupo mas NÃO têm a ver com a atividade das empresas (gastos pessoais/particulares
dos sócios e família pagos pela empresa). Funcionam como retirada de sócio:
saem do caixa mas não entram no resultado (DRE).

Fonte: grupo "Gastos Pessoais (Sócios)".

Uso:
    python gerar_relatorio_naooperacional.py
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import date, datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle, HRFlowable)

VINHO = colors.HexColor("#ad1457")
VINHO_ESC = colors.HexColor("#880e4f")
CINZA_T = colors.HexColor("#37474f")


def brl(v: float) -> str:
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def dt(s: str) -> str:
    return datetime.fromisoformat(s).strftime("%d/%m/%Y")


def main():
    con = sqlite3.connect("elaine.db")
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """SELECT l.data, l.contraparte, l.descricao, ABS(l.valor) AS valor,
                  p.nome AS cat, e.apelido AS emp
           FROM lancamentos l
           JOIN plano_contas p ON p.id = l.plano_conta_id
           LEFT JOIN empresas e ON e.id = l.empresa_id
           WHERE p.grupo LIKE '%cios%' AND l.tipo='saida'
           ORDER BY ABS(l.valor) DESC""",
    ).fetchall()

    if not rows:
        print("Sem gastos não-operacionais.")
        return

    total = sum(r["valor"] for r in rows)
    periodo_lo = min(r["data"] for r in rows)
    periodo_hi = max(r["data"] for r in rows)

    # resumo por tipo de gasto (categoria)
    por_cat = defaultdict(lambda: [0, 0.0])
    for r in rows:
        nome = r["cat"].replace("Pessoal - ", "")
        por_cat[nome][0] += 1
        por_cat[nome][1] += r["valor"]

    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Title"], fontSize=19, spaceAfter=2,
                        textColor=VINHO_ESC)
    sub = ParagraphStyle("sub", parent=styles["Normal"], fontSize=10.5,
                         textColor=colors.HexColor("#555555"), spaceAfter=2)
    sec = ParagraphStyle("sec", parent=styles["Heading2"], fontSize=13.5,
                         textColor=VINHO, spaceBefore=12, spaceAfter=4)
    nota = ParagraphStyle("nota", parent=styles["Normal"], fontSize=8.5,
                          textColor=colors.HexColor("#666666"), leading=12)

    elems = []
    elems.append(Paragraph("Gastos Não-Operacionais", h1))
    elems.append(Paragraph("Pagamentos que <b>não têm a ver com a atividade</b> das empresas "
                           "(gastos pessoais/particulares pagos pela empresa)", sub))
    elems.append(Paragraph(f"Grupo Edmundo &nbsp;·&nbsp; {dt(periodo_lo)} a {dt(periodo_hi)} "
                           f"&nbsp;·&nbsp; gerado em {date.today().strftime('%d/%m/%Y')}", sub))
    elems.append(Spacer(1, 4))
    elems.append(HRFlowable(width="100%", thickness=1.4, color=VINHO))
    elems.append(Spacer(1, 8))

    elems.append(Paragraph(
        f"<font size=9 color='#666666'>TOTAL NÃO-OPERACIONAL NO PERÍODO</font><br/>"
        f"<font size=18 color='#880e4f'><b>{brl(total)}</b></font>", styles["Normal"]))
    elems.append(Spacer(1, 8))

    # ── Resumo por tipo ───────────────────────────────────────────────────────
    elems.append(Paragraph("Por tipo de gasto", sec))
    resumo = [["Tipo de gasto", "Qtd", "Total"]]
    for nome, (q, v) in sorted(por_cat.items(), key=lambda x: x[1][1], reverse=True):
        resumo.append([nome, str(q), brl(v)])
    resumo.append(["TOTAL", str(len(rows)), brl(total)])
    t = Table(resumo, colWidths=[110 * mm, 25 * mm, 45 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), VINHO),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, colors.HexColor("#fbf0f5")]),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor("#dddddd")),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#f5dce7")),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    elems.append(t)

    # ── Detalhe lançamento a lançamento ───────────────────────────────────────
    elems.append(Paragraph("Detalhe de cada pagamento", sec))
    data = [["Data", "Beneficiário / Destino", "Tipo", "Empresa", "Valor"]]
    for r in rows:
        quem = (r["contraparte"] or r["descricao"] or "(sem nome)").strip()
        data.append([dt(r["data"]), quem[:38], r["cat"].replace("Pessoal - ", ""),
                     r["emp"] or "—", brl(r["valor"])])
    data.append(["", "TOTAL", "", "", brl(total)])
    dt_tbl = Table(data, colWidths=[20 * mm, 64 * mm, 35 * mm, 32 * mm, 29 * mm])
    dt_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), CINZA_T),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("ALIGN", (-1, 0), (-1, -1), "RIGHT"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, colors.HexColor("#f7f2f4")]),
        ("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.HexColor("#e0e0e0")),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#f5dce7")),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
    ]))
    elems.append(dt_tbl)

    # ── Rodapé ────────────────────────────────────────────────────────────────
    elems.append(Spacer(1, 10))
    elems.append(HRFlowable(width="100%", thickness=0.6, color=colors.HexColor("#cccccc")))
    elems.append(Spacer(1, 4))
    elems.append(Paragraph(
        "Estes valores são despesas particulares (família/sócios) quitadas com o caixa "
        "das empresas — financiamento e escola de filho, condomínio, internet residencial "
        "(Satuba), previdência, multa, limpeza de piscina, etc. Funcionam como retirada de "
        "sócio: <b>saem do caixa mas não entram no resultado (DRE)</b>, pois não são gasto "
        "da operação. Não inclui pró-labore (remuneração dos sócios) nem nenhum gasto da "
        "atividade das empresas.", nota))

    saida = f"Gastos_Nao_Operacionais_{date.today().isoformat()}.pdf"
    doc = SimpleDocTemplate(saida, pagesize=A4,
                            leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=14 * mm, bottomMargin=14 * mm,
                            title="Gastos Não-Operacionais — Grupo Edmundo")
    doc.build(elems)
    print("PDF gerado:", saida)
    print(f"Total não-operacional: {brl(total)} ({len(rows)} pagamentos)")


if __name__ == "__main__":
    main()
