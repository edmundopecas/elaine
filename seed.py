"""
Seed inicial: plano de contas padrão (BR), centros de custo e a empresa piloto.
Filipe ajusta tudo pela tela de Cadastros — isto é só um ponto de partida pra
não começar com as telas vazias.
"""
from __future__ import annotations

from db import execute, query_one

# Plano de contas padrão — (codigo, nome, grupo, tipo, entra_dre)
# entra_dre = 0 -> não é resultado (transferência interna, sócio, aplicação).
PLANO_CONTAS_PADRAO = [
    # RECEITAS
    ("3.1.01", "Receita de Vendas",            "Receitas",             "receita", 1),
    ("3.1.02", "Receita de Serviços",          "Receitas",             "receita", 1),
    ("3.1.03", "Outras Receitas",              "Receitas",             "receita", 1),
    # DEDUÇÕES DA RECEITA
    ("4.1.01", "Impostos sobre Vendas",        "Deduções",             "despesa", 1),
    ("4.1.02", "Taxas de Cartão/Adquirente",   "Deduções",             "despesa", 1),
    ("4.1.03", "Devoluções e Descontos",       "Deduções",             "despesa", 1),
    # CUSTOS
    ("5.1.01", "Custo de Mercadoria (CMV)",    "Custos",               "despesa", 1),
    ("5.1.02", "Custo de Material/Insumo",     "Custos",               "despesa", 1),
    # DESPESAS COM PESSOAL
    ("6.1.01", "Salários",                     "Despesas com Pessoal", "despesa", 1),
    ("6.1.02", "Encargos (INSS/FGTS)",         "Despesas com Pessoal", "despesa", 1),
    ("6.1.03", "Pró-labore",                   "Despesas com Pessoal", "despesa", 1),
    ("6.1.04", "Comissões",                    "Despesas com Pessoal", "despesa", 1),
    ("6.1.05", "Benefícios (VT/VR/Plano)",     "Despesas com Pessoal", "despesa", 1),
    # OCUPAÇÃO / ESTRUTURA
    ("6.2.01", "Aluguel",                      "Ocupação",             "despesa", 1),
    ("6.2.02", "Energia Elétrica",             "Ocupação",             "despesa", 1),
    ("6.2.03", "Água",                         "Ocupação",             "despesa", 1),
    ("6.2.04", "Internet/Telefone",            "Ocupação",             "despesa", 1),
    ("6.2.05", "Manutenção/Limpeza",           "Ocupação",             "despesa", 1),
    # ADMINISTRATIVAS
    ("6.3.01", "Contabilidade",                "Despesas Administrativas", "despesa", 1),
    ("6.3.02", "Material de Escritório",       "Despesas Administrativas", "despesa", 1),
    ("6.3.03", "Software/Assinaturas",         "Despesas Administrativas", "despesa", 1),
    ("6.3.04", "Despesas com Veículos",        "Despesas Administrativas", "despesa", 1),
    ("6.3.05", "Combustível",                  "Despesas Administrativas", "despesa", 1),
    # COMERCIAIS
    ("6.4.01", "Marketing/Publicidade",        "Despesas Comerciais",  "despesa", 1),
    ("6.4.02", "Fretes/Entregas",              "Despesas Comerciais",  "despesa", 1),
    # FINANCEIRAS
    ("6.5.01", "Tarifas Bancárias",            "Despesas Financeiras", "despesa", 1),
    ("6.5.02", "Juros/Multas",                 "Despesas Financeiras", "despesa", 1),
    ("6.5.03", "IOF",                          "Despesas Financeiras", "despesa", 1),
    # TRIBUTOS
    ("6.6.01", "Simples Nacional/DAS",         "Tributos",             "despesa", 1),
    ("6.6.02", "Outros Impostos/Taxas",        "Tributos",             "despesa", 1),
    # NÃO-RESULTADO (não entram na DRE) ------------------------------------
    ("9.1.01", "Transferência entre Empresas", "Movimentações Internas", "transferencia", 0),
    ("9.1.02", "Empréstimo/Aporte de Sócio",   "Movimentações Internas", "transferencia", 0),
    ("9.1.03", "Aplicação/Resgate Financeiro", "Movimentações Internas", "transferencia", 0),
    ("9.1.04", "Saque/Suprimento de Caixa",    "Movimentações Internas", "transferencia", 0),
]

CENTROS_CUSTO_PADRAO = [
    "Administrativo",
    "Operacional",
    "Comercial",
    "Financeiro",
]

# Empresas do grupo — (apelido, razao_social, cnpj só dígitos).
# Razões sociais aproximadas onde eu não tinha a oficial; Filipe ajusta na tela.
EMPRESAS_GRUPO = [
    ("Edmundo Matriz",   "EDMUNDO PEÇAS LTDA",        "06012511000100"),
    ("Edmundo Filial",   "EDMUNDO PEÇAS LTDA",        "06012511000290"),
    ("Rosilene",         "ROSILENE",                  "24883020000116"),
    ("Supernova",        "SUPERNOVA",                 "15528477000111"),
    ("EB Participações",  "EB PARTICIPAÇÕES LTDA",     "45872134000130"),
    ("Ferro Velho",      "FERRO VELHO DO BABY LTDA",  None),
]


def rodar_seed() -> None:
    for i, (codigo, nome, grupo, tipo, entra_dre) in enumerate(PLANO_CONTAS_PADRAO):
        execute(
            "INSERT INTO plano_contas (codigo, nome, grupo, tipo, entra_dre, ordem) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (codigo, nome, grupo, tipo, entra_dre, i),
        )

    for nome in CENTROS_CUSTO_PADRAO:
        execute("INSERT INTO centros_custo (nome) VALUES (?)", (nome,))

    # Empresas do grupo (idempotente: não duplica por CNPJ nem por apelido).
    for apelido, razao, cnpj in EMPRESAS_GRUPO:
        ja_existe = (
            query_one("SELECT 1 FROM empresas WHERE cnpj=?", (cnpj,)) if cnpj
            else query_one("SELECT 1 FROM empresas WHERE apelido=?", (apelido,))
        )
        if ja_existe:
            continue
        execute(
            "INSERT INTO empresas (razao_social, apelido, cnpj) VALUES (?, ?, ?)",
            (razao, apelido, cnpj),
        )


if __name__ == "__main__":
    from db import init_db
    init_db()
    print("Banco inicializado e seed aplicado.")
