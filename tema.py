"""
Paleta do projeto — Terracotta · Sage Green · Navy Blue (fundo creme).

Cores centralizadas pra todos os painéis usarem o mesmo visual. Se quiser trocar
a identidade do app, mexe só aqui (e no .streamlit/config.toml pro tema da página).

Os nomes "antigos" (FLORESTA, OCRE…) são mantidos como apelidos pra não quebrar
os imports dos painéis — apontam pras cores novas.
"""

# ── Cores-base da paleta ──────────────────────────────────────────────────────
TERRACOTA = "#BD6B4A"   # terracotta (laranja queimado)
SAGE = "#7E9576"        # sage green (verde-acinzentado)
NAVY = "#2C4D63"        # navy blue (azul-petróleo escuro)
CREME = "#F3EFE5"       # fundo creme

# ── Derivados harmônicos (mesma família) ──────────────────────────────────────
SAGE_CLARO = "#A0B39A"
TERRACOTA_CLARO = "#D08E72"
NAVY_CLARO = "#4F7089"
CINZA = "#A9A395"       # neutro quente
TEXTO = "#243A4A"       # texto escuro (navy) sobre creme
FUNDO = "#F3EFE5"       # fundo claro

# ── Apelidos retrocompatíveis (usados pelos painéis) ──────────────────────────
FLORESTA = SAGE                 # antes era verde-petróleo; agora sage
FLORESTA_CLARO = SAGE_CLARO
OCRE = NAVY                     # 3ª cor de gráfico = navy

# ── Papéis semânticos (use estes nos gráficos) ────────────────────────────────
POSITIVO = SAGE         # receita / sobra / despesa real
NEGATIVO = TERRACOTA    # despesa / déficit
NEUTRO = CINZA          # movimento interno / neutro
ATENCAO = NAVY          # a classificar / pendente
