"""
Gera um PDF com o detalhamento dos gastos com PESSOAL (folha), por categoria,
agrupando por pessoa. Para a diretoria/sócios.

Pega tudo do grupo "Despesas com Pessoal". Separa, no resumo, FOLHA DE
FUNCIONÁRIOS (salários, encargos, benefícios, 13º, férias, rescisão, pensão,
comissões, EPI) de SÓCIOS (pró-labore) — porque pró-labore não é folha de
funcionário e inflaria a leitura do custo de pessoal.

Uso:
    python gerar_relatorio_pessoal.py
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

ROXO = colors.HexColor("#4527a0")    # cor "pessoal"
ROXO_ESC = colors.HexColor("#311b92")
CINZA_T = colors.HexColor("#37474f")
ESCURO = colors.HexColor("#1b1b1b")

# grupo "Despesas com Pessoal" (ids confirmados no banco)
CATS = (9, 10, 11, 12, 13, 41, 49, 50, 51, 52)
PRO_LABORE = 11  # categoria de sócios (separada da folha de funcionários no resumo)


def brl(v: float) -> str:
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def dt(s: str) -> str:
    return datetime.fromisoformat(s).strftime("%d/%m/%Y")


def main():
    con = sqlite3.connect("elaine.db")
    con.row_factory = sqlite3.Row
    rows = con.execute(
        f"""SELECT l.data, l.contraparte, l.descricao, ABS(l.valor) AS valor,
                   l.plano_conta_id AS cat_id, p.nome AS cat, e.apelido AS emp
            FROM lancamentos l
            JOIN plano_contas p ON p.id = l.plano_conta_id
            LEFT JOIN empresas e ON e.id = l.empresa_id
            WHERE l.plano_conta_id IN ({','.join('?'*len(CATS))}) AND l.tipo='saida'
            ORDER BY l.data""",
        CATS,
    ).fetchall()

    if not rows:
        print("Sem lançamentos de pessoal.")
        return

    # agrupa por categoria -> pessoa (contraparte) -> [lançamentos]
    cats = defaultdict(lambda: defaultdict(list))
    cat_nome = {}
    for r in rows:
        cat_nome[r["cat_id"]] = r["cat"]
        quem = (r["contraparte"] or r["descricao"] or "(sem nome)").strip()
        cats[r["cat_id"]][quem].append(r)

    def total_cat(cid):
        return sum(x["valor"] for pes in cats[cid].values() for x in pes)

    total_geral = sum(r["valor"] for r in rows)
    total_folha = sum(total_cat(c) for c in cats if c != PRO_LABORE)
    total_socios = total_cat(PRO_LABORE) if PRO_LABORE in cats else 0.0
    periodo_lo = min(r["data"] for r in rows)
    periodo_hi = max(r["data"] for r in rows)

    # ── Estilos ───────────────────────────────────────────────────────────────
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Title"], fontSize=20, spaceAfter=2,
                        textColor=ROXO_ESC)
    sub = ParagraphStyle("sub", parent=styles["Normal"], fontSize=10.5,
                         textColor=colors.HexColor("#555555"), spaceAfter=2)
    sec = ParagraphStyle("sec", parent=styles["Heading2"], fontSize=14,
                         textColor=ROXO, spaceBefore=12, spaceAfter=4)
    subsec = ParagraphStyle("subsec", parent=styles["Heading3"], fontSize=11,
                            textColor=CINZA_T, spaceBefore=6, spaceAfter=2)
    nota = ParagraphStyle("nota", parent=styles["Normal"], fontSize=8.5,
                          textColor=colors.HexColor("#666666"), leading=12)

    elems = []
    elems.append(Paragraph("Detalhamento de Gastos com Pessoal", h1))
    elems.append(Paragraph(f"Grupo Edmundo &nbsp;·&nbsp; {dt(periodo_lo)} a {dt(periodo_hi)}", sub))
    elems.append(Paragraph(f"Relatório gerado em {date.today().strftime('%d/%m/%Y')}", sub))
    elems.append(Spacer(1, 4))
    elems.append(HRFlowable(width="100%", thickness=1.4, color=ROXO))
    elems.append(Spacer(1, 10))

    # ── Destaque: folha x sócios ──────────────────────────────────────────────
    destaque = Table([[
        Paragraph(f"<font size=8 color='#666666'>FOLHA DE FUNCIONÁRIOS</font><br/>"
                  f"<font size=15 color='#4527a0'><b>{brl(total_folha)}</b></font>", styles["Normal"]),
        Paragraph(f"<font size=8 color='#666666'>PRÓ-LABORE (SÓCIOS)</font><br/>"
                  f"<font size=15 color='#37474f'><b>{brl(total_socios)}</b></font>", styles["Normal"]),
        Paragraph(f"<font size=8 color='#666666'>TOTAL PESSOAL</font><br/>"
                  f"<font size=15 color='#1b1b1b'><b>{brl(total_geral)}</b></font>", styles["Normal"]),
    ]], colWidths=[60 * mm] * 3)
    destaque.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f3f0fa")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#d6cdef")),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e3ddf3")),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    elems.append(destaque)
    elems.append(Spacer(1, 10))

    # ── Resumo por categoria ──────────────────────────────────────────────────
    elems.append(Paragraph("Resumo por categoria", sec))
    resumo = [["Categoria", "Pessoas", "Lançam.", "Total"]]
    ordem = sorted(cats, key=total_cat, reverse=True)
    for cid in ordem:
        n_pes = len(cats[cid])
        n_lan = sum(len(v) for v in cats[cid].values())
        resumo.append([cat_nome[cid], str(n_pes), str(n_lan), brl(total_cat(cid))])
    resumo.append(["TOTAL", "", str(len(rows)), brl(total_geral)])

    t = Table(resumo, colWidths=[95 * mm, 24 * mm, 24 * mm, 38 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), ROXO),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, colors.HexColor("#f5f3fb")]),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor("#dddddd")),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#e7e1f5")),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    elems.append(t)

    # ── Detalhe por categoria (agrupado por pessoa) ───────────────────────────
    for cid in ordem:
        elems.append(Paragraph(f"{cat_nome[cid]} — {brl(total_cat(cid))}", sec))
        # consolida por pessoa: soma e conta pagamentos
        pessoas = []
        for quem, lans in cats[cid].items():
            tot = sum(x["valor"] for x in lans)
            emp = lans[0]["emp"] or "—"
            pessoas.append((quem, len(lans), emp, tot))
        pessoas.sort(key=lambda x: x[3], reverse=True)

        data = [["Funcionário / Beneficiário", "Empresa", "Pagtos", "Total"]]
        for quem, n, emp, tot in pessoas:
            data.append([quem[:50], emp, str(n), brl(tot)])
        dt_tbl = Table(data, colWidths=[88 * mm, 38 * mm, 20 * mm, 35 * mm])
        dt_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), CINZA_T),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f2f7")]),
            ("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.HexColor("#e0e0e0")),
            ("TOPPADDING", (0, 0), (-1, -1), 3.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ]))
        elems.append(dt_tbl)
        elems.append(Spacer(1, 2))
        elems.append(Paragraph(
            f"<b>{len(pessoas)} pessoa(s) · {brl(total_cat(cid))}</b>", nota))

    # ── Rodapé ────────────────────────────────────────────────────────────────
    elems.append(Spacer(1, 8))
    elems.append(HRFlowable(width="100%", thickness=0.6, color=colors.HexColor("#cccccc")))
    elems.append(Spacer(1, 4))
    elems.append(Paragraph(
        "<b>Folha de funcionários</b> reúne salários, encargos, benefícios, 13º, férias, "
        "rescisões, pensão e comissões. <b>Pró-labore</b> é a remuneração dos sócios e está "
        "separado por não ser custo de folha de funcionário. Valores extraídos diretamente "
        "dos extratos bancários do grupo, agrupados por beneficiário.", nota))

    saida = f"Pessoal_Folha_{date.today().isoformat()}.pdf"
    doc = SimpleDocTemplate(saida, pagesize=A4,
                            leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=14 * mm, bottomMargin=14 * mm,
                            title="Detalhamento de Gastos com Pessoal — Grupo Edmundo")
    doc.build(elems)
    print("PDF gerado:", saida)
    print(f"Folha funcionários: {brl(total_folha)} | Pró-labore (sócios): {brl(total_socios)} "
          f"| Total pessoal: {brl(total_geral)}")


if __name__ == "__main__":
    main()
