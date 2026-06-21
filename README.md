# Elaine — Controle Financeiro do Grupo

Controle de **contas a pagar/receber, DRE e fluxo de caixa por empresa**,
separado do sistema. Alimentado pelos extratos bancários, com classificação
automática que aprende.

## Como rodar (na sua máquina)

```powershell
cd C:\Users\Elaine\elaine
pip install -r requirements.txt      # só na 1ª vez
streamlit run app.py
```

Abre sozinho no navegador. O banco (`elaine.db`) é criado no primeiro acesso,
já com plano de contas, centros de custo e a empresa Ferro Velho.

## Fluxo de uso

1. **Cadastros** — cadastre as demais empresas do grupo e as contas bancárias.
2. **Importar Extrato** — suba o CSV ou OFX do banco. O que as regras já
   conhecem entra classificado; o resto fica pendente.
3. **Classificar** — resolva os pendentes. Ao classificar, marque "criar regra"
   pra que os próximos iguais entrem sozinhos.
4. **DRE** — resultado por empresa e período (transferências internas ficam de fora).
5. **Fluxo de Caixa** — realizado (extratos) + previsto (contas a pagar/receber).
6. **Contas a Pagar/Receber** — cadastre títulos com vencimento e dê baixa.

## Arquitetura

| Arquivo | Papel |
|---|---|
| `schema.sql` | Estrutura do banco |
| `db.py` | Conexão SQLite + init/seed |
| `seed.py` | Plano de contas e centros padrão |
| `parsers.py` | Leitura de extrato CSV/OFX (PDF na próxima rodada) |
| `classificador.py` | Motor de regras de-para que aprende |
| `app.py` + `pages/` | Telas Streamlit |

## Próximos passos

- [ ] Leitor de PDF de extrato (varia por banco)
- [ ] Conciliação: baixar título automático quando casar com lançamento do extrato
- [ ] Deploy no Railway + Supabase (multiusuário pra diretoria)
- [ ] Exportar DRE/fluxo em PDF
