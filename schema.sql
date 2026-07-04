-- ─────────────────────────────────────────────────────────────────────────────
-- Elaine — Controle Financeiro do Grupo (contas a pagar/receber, DRE, fluxo)
-- Banco: SQLite (dev). O SQL é compatível em ~95% com Postgres/Supabase pro deploy.
-- ─────────────────────────────────────────────────────────────────────────────

-- Empresas do grupo ----------------------------------------------------------
CREATE TABLE IF NOT EXISTS empresas (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    razao_social TEXT NOT NULL,
    apelido      TEXT NOT NULL,
    cnpj         TEXT,
    ativa        INTEGER NOT NULL DEFAULT 1,
    criada_em    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Contas bancárias (uma empresa pode ter várias) -----------------------------
CREATE TABLE IF NOT EXISTS contas_bancarias (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    empresa_id  INTEGER NOT NULL REFERENCES empresas(id),
    banco       TEXT NOT NULL,
    descricao   TEXT,                 -- ex: "BB C/C 12345-6"
    ativa       INTEGER NOT NULL DEFAULT 1
);

-- Centros de custo -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS centros_custo (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    nome   TEXT NOT NULL,
    ativo  INTEGER NOT NULL DEFAULT 1
);

-- Plano de contas (as categorias que viram a DRE) ----------------------------
-- tipo: 'receita' | 'despesa' | 'transferencia'
-- entra_dre = 0  -> movimento que NÃO é resultado (transferência entre empresas,
--                   empréstimo de sócio, aplicação/resgate). Aparece no fluxo de
--                   caixa, mas é excluído da DRE.
CREATE TABLE IF NOT EXISTS plano_contas (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    codigo     TEXT,
    nome       TEXT NOT NULL,
    grupo      TEXT NOT NULL,         -- ex: "Despesas Operacionais"
    tipo       TEXT NOT NULL,         -- 'receita' | 'despesa' | 'transferencia'
    entra_dre  INTEGER NOT NULL DEFAULT 1,
    ordem      INTEGER NOT NULL DEFAULT 0,
    ativo      INTEGER NOT NULL DEFAULT 1
);

-- Regras de classificação (de-para que aprende com o uso) --------------------
-- Quando um histórico bate no padrão, sugere empresa/categoria/centro de custo.
CREATE TABLE IF NOT EXISTS regras_classificacao (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    padrao          TEXT NOT NULL,            -- texto normalizado a procurar no histórico
    tipo_match      TEXT NOT NULL DEFAULT 'contem',  -- 'contem' | 'igual'
    empresa_id      INTEGER REFERENCES empresas(id),  -- NULL = qualquer empresa
    aplica_tipo     TEXT,                     -- 'entrada' | 'saida' | NULL (ambos)
    plano_conta_id  INTEGER REFERENCES plano_contas(id),
    centro_custo_id INTEGER REFERENCES centros_custo(id),
    prioridade      INTEGER NOT NULL DEFAULT 0,
    vezes_aplicada  INTEGER NOT NULL DEFAULT 0,
    ativa           INTEGER NOT NULL DEFAULT 1,
    criada_em       TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Importações (cada arquivo de extrato lido) ---------------------------------
CREATE TABLE IF NOT EXISTS importacoes (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    empresa_id         INTEGER REFERENCES empresas(id),
    conta_bancaria_id  INTEGER REFERENCES contas_bancarias(id),
    arquivo_nome       TEXT,
    arquivo_hash       TEXT,
    formato            TEXT,
    linhas_total       INTEGER,
    linhas_importadas  INTEGER,
    linhas_duplicadas  INTEGER,
    criado_em          TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Lançamentos = linhas de extrato (o REALIZADO) ------------------------------
CREATE TABLE IF NOT EXISTS lancamentos (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    empresa_id        INTEGER REFERENCES empresas(id),
    conta_bancaria_id INTEGER REFERENCES contas_bancarias(id),
    data              TEXT NOT NULL,            -- ISO 'YYYY-MM-DD'
    descricao         TEXT,                     -- histórico do extrato
    contraparte       TEXT,                     -- nome de quem pagou/recebeu
    cnpj_contraparte  TEXT,                     -- CNPJ/CPF da contraparte (só dígitos)
    documento         TEXT,
    valor             REAL NOT NULL,            -- sempre positivo
    tipo              TEXT NOT NULL,            -- 'entrada' | 'saida'
    plano_conta_id    INTEGER REFERENCES plano_contas(id),
    centro_custo_id   INTEGER REFERENCES centros_custo(id),
    classificado      INTEGER NOT NULL DEFAULT 0,
    origem            TEXT NOT NULL DEFAULT 'extrato',  -- 'extrato' | 'manual'
    regra_id          INTEGER REFERENCES regras_classificacao(id),
    importacao_id     INTEGER REFERENCES importacoes(id),
    linha_hash        TEXT,
    saldo_apos        REAL,
    observacao        TEXT,
    criado_em         TEXT NOT NULL DEFAULT (datetime('now'))
);
-- Dedup: a mesma linha de extrato não entra duas vezes.
CREATE UNIQUE INDEX IF NOT EXISTS idx_lanc_hash
    ON lancamentos(linha_hash) WHERE linha_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_lanc_data    ON lancamentos(data);
CREATE INDEX IF NOT EXISTS idx_lanc_empresa ON lancamentos(empresa_id);

-- Títulos = contas a PAGAR / RECEBER (o PREVISTO) ----------------------------
-- Também é o destino do relatório "A Pagar Geral" (Argos): cada título é uma
-- conta a pagar prevista; a baixa (lancamento_id) é o VÍNCULO com o pagamento
-- realizado no extrato — é isso que a tela de Conferência preenche.
CREATE TABLE IF NOT EXISTS titulos (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    empresa_id      INTEGER REFERENCES empresas(id),  -- NULL p/ Braga (fora do app)
    tipo            TEXT NOT NULL,            -- 'pagar' | 'receber'
    descricao       TEXT NOT NULL,
    contraparte     TEXT,                     -- fornecedor / cliente
    plano_conta_id  INTEGER REFERENCES plano_contas(id),
    centro_custo_id INTEGER REFERENCES centros_custo(id),
    valor           REAL NOT NULL,
    vencimento      TEXT NOT NULL,            -- ISO 'YYYY-MM-DD'
    status          TEXT NOT NULL DEFAULT 'aberto',  -- 'aberto'|'pago'|'recebido'|'cancelado'
    data_baixa      TEXT,
    lancamento_id   INTEGER REFERENCES lancamentos(id),  -- baixa = liga ao extrato
    documento       TEXT,                     -- nº do documento no Argos
    tipo_docto      TEXT,                     -- MERCADORIA, TRIBUTOS, PESSOAL...
    loja            TEXT,                     -- loja do Argos (razão social)
    origem          TEXT DEFAULT 'manual',    -- 'argos' quando vem da A Pagar Geral
    linha_hash      TEXT,                     -- dedup em reimport
    criado_em       TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_titulos_venc    ON titulos(vencimento);
CREATE INDEX IF NOT EXISTS idx_titulos_empresa ON titulos(empresa_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_titulos_hash
    ON titulos(linha_hash) WHERE linha_hash IS NOT NULL;
