"""
Gera um PDF SÓ com os gastos relacionados aos SÓCIOS (não inclui funcionários).
Duas partes:
  1) Pró-labore — remuneração dos sócios (entra na DRE)
  2) Gastos pessoais dos sócios pagos pela empresa (escola, financiamento,
     condomínio, internet, previdência, etc.) — FORA da DRE (são retiradas).

Uso:
    python gerar_relatorio_socios.py
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime

from db import query   # lê do banco ativo (Supabase se ELAINE_DATABASE_URL setada)

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle, HRFlowable)

ROXO = colors.HexColor("#4527a0")
ROXO_ESC = colors.HexColor("#311b92")
CINZA_T = colors.HexColor("#37474f")
ESCURO = colors.HexColor("#1b1b1b")

CAT_PROLABORE = 11
# "gastos pessoais" = todo o grupo Gastos Pessoais (Sócios), pego dinamicamente
# pelo grupo (assim categorias novas — ex: Pessoal - Família — entram sozinhas).


def brl(v: float) -> str:
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def dt(s: str) -> str:
    return datetime.fromisoformat(s).strftime("%d/%m/%Y")


import re

_PREFIXOS = re.compile(
    r"^(pagamentos? a fornecedores|pix enviado(\s*-)?|pix recebido(\s*-)?|"
    r"t[íi]tulo/boleto( de outros bancos| ita[úu])?(\s*-)?|"
    r"transfer[êe]ncia( enviada)?|ted|doc)\s*",
    re.IGNORECASE,
)


def _limpa_nome(s: str) -> str:
    """Tira prefixos de banco/tipo do começo do nome (PAGAMENTOS A FORNECEDORES,
    Pix Enviado, Título/boleto …) pra sobrar só o nome do beneficiário."""
    s = (s or "").strip()
    novo = _PREFIXOS.sub("", s).strip(" -–—")
    return (novo or s).title()


def main():
    rows = query(
        """SELECT l.data, l.contraparte, l.descricao, ABS(l.valor) AS valor,
                  l.plano_conta_id AS cat_id, p.nome AS cat, e.apelido AS emp
           FROM lancamentos l
           JOIN plano_contas p ON p.id = l.plano_conta_id
           LEFT JOIN empresas e ON e.id = l.empresa_id
           WHERE (l.plano_conta_id = ? OR p.grupo LIKE '%cios%') AND l.tipo='saida'
           ORDER BY l.data""",
        (CAT_PROLABORE,),
    )

    if not rows:
        print("Sem lançamentos de sócios.")
        return

    prolab = [r for r in rows if r["cat_id"] == CAT_PROLABORE]
    pessoais = [r for r in rows if r["cat_id"] != CAT_PROLABORE]
    tot_prolab = sum(r["valor"] for r in prolab)
    tot_pess = sum(r["valor"] for r in pessoais)
    total = tot_prolab + tot_pess
    periodo_lo = min(r["data"] for r in rows)
    periodo_hi = max(r["data"] for r in rows)

    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Title"], fontSize=20, spaceAfter=2,
                        textColor=ROXO_ESC)
    sub = ParagraphStyle("sub", parent=styles["Normal"], fontSize=10.5,
                         textColor=colors.HexColor("#555555"), spaceAfter=2)
    sec = ParagraphStyle("sec", parent=styles["Heading2"], fontSize=14,
                         textColor=ROXO, spaceBefore=12, spaceAfter=4)
    nota = ParagraphStyle("nota", parent=styles["Normal"], fontSize=8.5,
                          textColor=colors.HexColor("#666666"), leading=12)
    cel = ParagraphStyle("cel", parent=styles["Normal"], fontSize=8.5,
                         textColor=ESCURO, leading=10)
    cel_b = ParagraphStyle("cel_b", parent=cel, fontName="Helvetica-Bold")

    elems = []
    elems.append(Paragraph("Gastos Finais dos Sócios", h1))
    elems.append(Paragraph("Pró-labore (remuneração) + gastos pessoais pagos pela empresa", sub))
    elems.append(Paragraph(f"Grupo Edmundo &nbsp;·&nbsp; {dt(periodo_lo)} a {dt(periodo_hi)}", sub))
    elems.append(Paragraph(f"Relatório gerado em {date.today().strftime('%d/%m/%Y')}", sub))
    elems.append(Spacer(1, 4))
    elems.append(HRFlowable(width="100%", thickness=1.4, color=ROXO))
    elems.append(Spacer(1, 10))

    # ── Destaque ──────────────────────────────────────────────────────────────
    destaque = Table([[
        Paragraph("<font size=8 color='#666666'>PRÓ-LABORE (REMUNERAÇÃO)</font><br/>"
                  f"<font size=15 color='#4527a0'><b>{brl(tot_prolab)}</b></font>", styles["Normal"]),
        Paragraph("<font size=8 color='#666666'>GASTOS PESSOAIS PAGOS PELA EMPRESA</font><br/>"
                  f"<font size=15 color='#37474f'><b>{brl(tot_pess)}</b></font>", styles["Normal"]),
        Paragraph("<font size=8 color='#666666'>TOTAL SÓCIOS</font><br/>"
                  f"<font size=15 color='#1b1b1b'><b>{brl(total)}</b></font>", styles["Normal"]),
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
    elems.append(Spacer(1, 6))

    def tabela_por_pessoa(itens, titulo, com_categoria=False):
        """Agrupa por pessoa (e categoria, se pedido) e desenha tabela."""
        elems.append(Paragraph(titulo, sec))
        chave = defaultdict(list)
        for r in itens:
            quem = _limpa_nome(r["contraparte"] or r["descricao"] or "(sem nome)")
            k = (quem, r["cat"]) if com_categoria else (quem,)
            chave[k].append(r)
        linhas = []
        for k, lans in chave.items():
            tot = sum(x["valor"] for x in lans)
            emp = lans[0]["emp"] or "—"
            linhas.append((k, len(lans), emp, tot))
        linhas.sort(key=lambda x: x[3], reverse=True)

        if com_categoria:
            head = ["Beneficiário", "Tipo de gasto", "Empresa", "Qtd", "Total"]
            colw = [52 * mm, 40 * mm, 30 * mm, 14 * mm, 32 * mm]
        else:
            head = ["Sócio / Beneficiário", "Empresa", "Qtd", "Total"]
            colw = [85 * mm, 38 * mm, 18 * mm, 35 * mm]
        # cabeçalho em Paragraph branco; células de texto em Paragraph que QUEBRAM linha
        head_st = ParagraphStyle("h", parent=cel_b, textColor=colors.white)
        data = [[Paragraph(h, head_st) for h in head]]
        for k, n, emp, tot in linhas:
            if com_categoria:
                data.append([Paragraph(k[0], cel),
                             Paragraph(k[1].replace("Pessoal - ", ""), cel),
                             Paragraph(emp, cel), str(n), brl(tot)])
            else:
                data.append([Paragraph(k[0], cel), Paragraph(emp, cel), str(n), brl(tot)])
        tot_geral = sum(x[3] for x in linhas)
        data.append([Paragraph("TOTAL", cel_b)] + [""] * (len(head) - 2) + [brl(tot_geral)])

        tbl = Table(data, colWidths=colw)
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), CINZA_T),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN", (-2, 0), (-1, -1), "RIGHT"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, colors.HexColor("#f4f2f7")]),
            ("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.HexColor("#e0e0e0")),
            ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#e7e1f5")),
            ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
            ("TOPPADDING", (0, 0), (-1, -1), 3.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ]))
        elems.append(tbl)

    # ── Parte 1: Pró-labore ───────────────────────────────────────────────────
    if prolab:
        tabela_por_pessoa(prolab, "1. Pró-labore — remuneração dos sócios")
        elems.append(Paragraph(
            "Pró-labore é a remuneração mensal dos sócios pela administração. "
            "Entra no resultado da empresa (DRE).", nota))

    # ── Parte 2: Gastos pessoais ──────────────────────────────────────────────
    if pessoais:
        tabela_por_pessoa(pessoais,
                          "2. Gastos pessoais dos sócios pagos pela empresa",
                          com_categoria=True)
        elems.append(Paragraph(
            "São despesas particulares dos sócios (escola, financiamento, condomínio, "
            "internet, previdência, etc.) quitadas pela empresa. Funcionam como retirada "
            "de sócio: saem do caixa, mas <b>não entram no resultado (DRE)</b> por não "
            "serem despesa da operação.", nota))

    # ── Rodapé ────────────────────────────────────────────────────────────────
    elems.append(Spacer(1, 8))
    elems.append(HRFlowable(width="100%", thickness=0.6, color=colors.HexColor("#cccccc")))
    elems.append(Spacer(1, 4))
    elems.append(Paragraph(
        f"No período, os sócios consumiram <b>{brl(total)}</b> do caixa do grupo — "
        f"{brl(tot_prolab)} de pró-labore e {brl(tot_pess)} em gastos pessoais pagos "
        "pela empresa. Não inclui nenhum gasto com funcionários.", nota))

    saida = f"Gastos_Finais_Socios_{date.today().isoformat()}.pdf"
    doc = SimpleDocTemplate(saida, pagesize=A4,
                            leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=14 * mm, bottomMargin=14 * mm,
                            title="Gastos com os Sócios — Grupo Edmundo")
    doc.build(elems)
    print("PDF gerado:", saida)
    print(f"Pró-labore: {brl(tot_prolab)} | Gastos pessoais: {brl(tot_pess)} | Total sócios: {brl(total)}")


if __name__ == "__main__":
    main()
