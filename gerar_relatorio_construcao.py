"""
Gera um PDF com o detalhamento dos gastos com CONSTRUÇÃO, por obra, separando
mão de obra x material, com o detalhe de cada pagamento. Para a diretoria/sócios.

Pega tudo do grupo "Construção" (obras com centro de custo) + a categoria antiga
"Construção/Reformas" (gastos avulsos sem obra definida).

Uso:
    python gerar_relatorio_construcao.py
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

LARANJA = colors.HexColor("#e65100")   # cor "construção"
MARROM = colors.HexColor("#5d4037")
AZUL = colors.HexColor("#1565c0")
ESCURO = colors.HexColor("#1b1b1b")

# categorias que compõem "construção"
CATS = (40, 53, 54, 55)


def brl(v: float) -> str:
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def dt(s: str) -> str:
    return datetime.fromisoformat(s).strftime("%d/%m/%Y")


def tipo_de(cat_nome: str) -> str:
    n = cat_nome.lower()
    if "mão de obra" in n or "mao de obra" in n:
        return "Mão de Obra"
    if "material" in n:
        return "Material"
    return "Outros (avulso)"


def main():
    con = sqlite3.connect("elaine.db")
    con.row_factory = sqlite3.Row
    rows = con.execute(
        f"""SELECT l.data, l.contraparte, l.descricao, ABS(l.valor) AS valor,
                   p.nome AS cat, COALESCE(cc.nome, 'Avulso (sem obra definida)') AS obra,
                   e.apelido AS emp
            FROM lancamentos l
            JOIN plano_contas p ON p.id = l.plano_conta_id
            LEFT JOIN centros_custo cc ON cc.id = l.centro_custo_id
            LEFT JOIN empresas e ON e.id = l.empresa_id
            WHERE l.plano_conta_id IN ({','.join('?'*len(CATS))}) AND l.tipo='saida'
            ORDER BY l.data""",
        CATS,
    ).fetchall()

    if not rows:
        print("Sem lançamentos de construção.")
        return

    # agrupa por obra -> tipo -> lista de lançamentos
    obras = defaultdict(lambda: defaultdict(list))
    for r in rows:
        obras[r["obra"]][tipo_de(r["cat"])].append(r)

    total_geral = sum(r["valor"] for r in rows)
    periodo_lo = min(r["data"] for r in rows)
    periodo_hi = max(r["data"] for r in rows)

    # ── Estilos ───────────────────────────────────────────────────────────────
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Title"], fontSize=20, spaceAfter=2,
                        textColor=MARROM)
    sub = ParagraphStyle("sub", parent=styles["Normal"], fontSize=10.5,
                         textColor=colors.HexColor("#555555"), spaceAfter=2)
    sec = ParagraphStyle("sec", parent=styles["Heading2"], fontSize=14,
                         textColor=LARANJA, spaceBefore=12, spaceAfter=4)
    subsec = ParagraphStyle("subsec", parent=styles["Heading3"], fontSize=11,
                            textColor=MARROM, spaceBefore=6, spaceAfter=2)
    nota = ParagraphStyle("nota", parent=styles["Normal"], fontSize=8.5,
                          textColor=colors.HexColor("#666666"), leading=12)

    elems = []
    elems.append(Paragraph("Detalhamento de Obras / Construção", h1))
    elems.append(Paragraph(f"Grupo Edmundo &nbsp;·&nbsp; {dt(periodo_lo)} a {dt(periodo_hi)}", sub))
    elems.append(Paragraph(f"Relatório gerado em {date.today().strftime('%d/%m/%Y')}", sub))
    elems.append(Spacer(1, 4))
    elems.append(HRFlowable(width="100%", thickness=1.4, color=LARANJA))
    elems.append(Spacer(1, 10))

    # ── Resumo por obra ───────────────────────────────────────────────────────
    elems.append(Paragraph("Resumo por obra", sec))
    resumo = [["Obra", "Mão de Obra", "Material", "Outros", "Total"]]
    ordem_obras = sorted(obras, key=lambda o: sum(x["valor"] for t in obras[o].values() for x in t),
                         reverse=True)
    for obra in ordem_obras:
        mo = sum(x["valor"] for x in obras[obra].get("Mão de Obra", []))
        mat = sum(x["valor"] for x in obras[obra].get("Material", []))
        out = sum(x["valor"] for x in obras[obra].get("Outros (avulso)", []))
        resumo.append([obra, brl(mo) if mo else "—", brl(mat) if mat else "—",
                       brl(out) if out else "—", brl(mo + mat + out)])
    tot_mo = sum(x["valor"] for o in obras.values() for x in o.get("Mão de Obra", []))
    tot_mat = sum(x["valor"] for o in obras.values() for x in o.get("Material", []))
    tot_out = sum(x["valor"] for o in obras.values() for x in o.get("Outros (avulso)", []))
    resumo.append(["TOTAL", brl(tot_mo), brl(tot_mat), brl(tot_out) if tot_out else "—",
                   brl(total_geral)])

    t = Table(resumo, colWidths=[55 * mm, 31 * mm, 31 * mm, 25 * mm, 33 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), LARANJA),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, colors.HexColor("#fbf3ec")]),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor("#dddddd")),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#f3e3d3")),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    elems.append(t)
    elems.append(Spacer(1, 6))
    elems.append(Paragraph(
        f"Total investido em obras no período: <b>{brl(total_geral)}</b> "
        f"(mão de obra {brl(tot_mo)} · material {brl(tot_mat)}"
        + (f" · avulsos {brl(tot_out)}" if tot_out else "") + ").", nota))

    # ── Detalhe por obra ──────────────────────────────────────────────────────
    for obra in ordem_obras:
        elems.append(Paragraph(f"Obra: {obra}", sec))
        obra_total = sum(x["valor"] for t in obras[obra].values() for x in t)

        for tipo in ("Mão de Obra", "Material", "Outros (avulso)"):
            itens = obras[obra].get(tipo, [])
            if not itens:
                continue
            sub_t = sum(x["valor"] for x in itens)
            elems.append(Paragraph(f"{tipo} — {brl(sub_t)}", subsec))
            data = [["Data", "Fornecedor / Destino", "Empresa", "Valor"]]
            for r in sorted(itens, key=lambda x: x["data"]):
                quem = (r["contraparte"] or r["descricao"] or "(sem nome)").strip()
                data.append([dt(r["data"]), quem[:48], r["emp"] or "—", brl(r["valor"])])
            dt_tbl = Table(data, colWidths=[22 * mm, 80 * mm, 38 * mm, 35 * mm])
            dt_tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), MARROM),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                ("ALIGN", (3, 0), (3, -1), "RIGHT"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7f2ee")]),
                ("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.HexColor("#e0e0e0")),
                ("TOPPADDING", (0, 0), (-1, -1), 3.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
            ]))
            elems.append(dt_tbl)
            elems.append(Spacer(1, 4))

        elems.append(Paragraph(f"<b>Subtotal {obra}: {brl(obra_total)}</b>", nota))
        elems.append(Spacer(1, 4))

    # ── Rodapé ────────────────────────────────────────────────────────────────
    elems.append(Spacer(1, 6))
    elems.append(HRFlowable(width="100%", thickness=0.6, color=colors.HexColor("#cccccc")))
    elems.append(Spacer(1, 4))
    elems.append(Paragraph(
        "Cada obra é acompanhada por centro de custo, com os gastos separados entre "
        "mão de obra e material. Valores extraídos diretamente dos extratos bancários "
        "do grupo.", nota))

    saida = f"Construcao_Obras_{date.today().isoformat()}.pdf"
    doc = SimpleDocTemplate(saida, pagesize=A4,
                            leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=14 * mm, bottomMargin=14 * mm,
                            title="Detalhamento de Obras / Construção — Grupo Edmundo")
    doc.build(elems)
    print("PDF gerado:", saida)
    print(f"Total construção: {brl(total_geral)} "
          f"(mão de obra {brl(tot_mo)} · material {brl(tot_mat)} · avulsos {brl(tot_out)})")


if __name__ == "__main__":
    main()
