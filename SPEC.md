# Prompt para Claude Code — Crypto Top 50 Quant Tracker

> **Cómo usar este archivo:**
> 1. Crea la carpeta del proyecto: `mkdir crypto-top50 && cd crypto-top50`
> 2. Guarda este archivo dentro como `SPEC.md`
> 3. Abre Claude Code en esa carpeta: `claude`
> 4. Pega el bloque "PROMPT INICIAL" de abajo
> 5. Después ve pidiendo las fases una por una

---

## PROMPT INICIAL (pega esto tal cual)

```
Lee SPEC.md en esta carpeta. Es la especificación completa de lo que quiero construir.

Antes de escribir código:
1. Confirma que entendiste el objetivo en 3-4 bullets
2. Propón la estructura de archivos que vas a crear
3. Dime qué decisiones técnicas tomarías distinto y por qué

No escribas código todavía. Espera mi OK.
```

---

# SPEC: Crypto Top 50 Quantitative Tracker

## 1. Objetivo

Sistema en Python que rastrea las **top 50 criptomonedas por market cap** y genera análisis puramente cuantitativo sobre:

- **Permanencia**: cuánto tiempo lleva cada moneda dentro del top 50, cuántas veces ha entrado y salido, duración promedio por estadía
- **Retornos post-entrada**: retorno % a los 20, 50, 100 y 200 días desde que una moneda entra al top 50
- **Composición por categoría**: qué % del top 50 es Layer 1, Layer 2, DeFi, Stablecoin, Meme, AI, etc.
- **Performance por categoría**: retorno promedio y permanencia promedio agrupado por tipo de moneda

La tesis del proyecto: *entrar al top 50 es una señal medible. ¿Qué pasa después? ¿Qué categorías sobreviven y cuáles son flashes?*

---

## 2. Stack

| Componente | Tecnología | Por qué |
|---|---|---|
| Lenguaje | Python 3.11+ | ecosistema de datos |
| Datos de mercado | CoinGecko API (tier gratuito) | sin API key, histórico desde 2013 |
| Almacenamiento | SQLite | histórico persistente, queries SQL, un solo archivo |
| Análisis | pandas | agregaciones por categoría |
| Config | archivo `.yaml` o `.toml` | categorías editables sin tocar código |
| CLI | `argparse` o `typer` | comandos claros |
| Dashboard | HTML estático + Chart.js | abrible sin servidor, fácil de compartir |

**Restricción importante:** el tier gratuito de CoinGecko permite ~30 requests/minuto. El sistema debe respetar esto con rate limiting automático y reintentos con backoff.

---

## 3. Estructura de archivos objetivo

```
crypto-top50/
├── SPEC.md
├── README.md
├── requirements.txt
├── config/
│   └── categories.yaml       # mapeo coin_id → categoría
├── src/
│   ├── __init__.py
│   ├── db.py                 # esquema SQLite + queries
│   ├── coingecko.py          # cliente API con rate limiting
│   ├── tracker.py            # lógica de entradas/salidas del top 50
│   ├── returns.py            # cálculo de retornos d20/d50/d100/d200
│   ├── analytics.py          # agregaciones por categoría (pandas)
│   └── export.py             # genera JSON/CSV para el dashboard
├── dashboard/
│   ├── index.html
│   ├── style.css
│   └── app.js
├── data/
│   └── tracker.db            # SQLite (gitignored)
├── output/
│   ├── analysis.json
│   └── returns.csv
├── tests/
│   └── test_returns.py
└── main.py                   # CLI entrypoint
```

---

## 4. Esquema de base de datos

```sql
-- Catálogo de monedas
CREATE TABLE coins (
    coin_id      TEXT PRIMARY KEY,
    symbol       TEXT NOT NULL,
    name         TEXT NOT NULL,
    category     TEXT,
    first_seen   DATE
);

-- Snapshot diario del top 50
CREATE TABLE snapshots (
    snapshot_date DATE NOT NULL,
    coin_id       TEXT NOT NULL,
    rank          INTEGER NOT NULL,
    price_usd     REAL,
    market_cap    REAL,
    volume_24h    REAL,
    PRIMARY KEY (snapshot_date, coin_id),
    FOREIGN KEY (coin_id) REFERENCES coins(coin_id)
);

-- Períodos de permanencia (una fila por "estadía" en el top 50)
CREATE TABLE tenures (
    tenure_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    coin_id       TEXT NOT NULL,
    entry_date    DATE NOT NULL,
    entry_price   REAL NOT NULL,
    entry_rank    INTEGER,
    exit_date     DATE,              -- NULL si sigue dentro
    exit_price    REAL,
    days_in_top50 INTEGER,
    FOREIGN KEY (coin_id) REFERENCES coins(coin_id)
);

-- Retornos calculados por hito
CREATE TABLE returns (
    tenure_id     INTEGER NOT NULL,
    milestone_day INTEGER NOT NULL,  -- 20, 50, 100, 200
    price_at_day  REAL,
    return_pct    REAL,
    computed_at   TIMESTAMP,
    PRIMARY KEY (tenure_id, milestone_day),
    FOREIGN KEY (tenure_id) REFERENCES tenures(tenure_id)
);

-- Cache de precios históricos (evita re-llamar la API)
CREATE TABLE price_cache (
    coin_id    TEXT NOT NULL,
    price_date DATE NOT NULL,
    price_usd  REAL,
    PRIMARY KEY (coin_id, price_date)
);
```

**Índices sugeridos:** `snapshots(coin_id)`, `tenures(coin_id, exit_date)`, `price_cache(coin_id)`.

---

## 5. Lógica central: detección de entradas y salidas

Este es el corazón del sistema. Cada vez que se corre un snapshot:

```
top50_hoy   = set de coin_ids en el top 50 de hoy
top50_ayer  = set de coin_ids del último snapshot

ENTRADAS = top50_hoy - top50_ayer
    → crear nueva fila en `tenures` con entry_date = hoy, entry_price = precio actual

SALIDAS = top50_ayer - top50_hoy
    → cerrar el tenure abierto: exit_date = hoy, calcular days_in_top50

PERMANECEN = top50_hoy ∩ top50_ayer
    → solo actualizar days_in_top50 del tenure abierto
```

**Caso borde a manejar:** si hay un gap en los snapshots (no corriste el script por una semana), no asumir continuidad. Registrar el gap y marcar los tenures afectados con un flag `has_gap`.

---

## 6. Backfill histórico

El problema: si empiezo hoy, no tengo historia. Solución en dos modos:

### Modo A — Backfill sintético (recomendado para arrancar)
Usar el endpoint de CoinGecko `/coins/{id}/market_chart/range` para reconstruir el market cap histórico de las ~150 monedas más grandes, y **recalcular el top 50 día por día hacia atrás** durante los últimos 2-3 años.

Esto genera de golpe años de tenures y retornos reales sin esperar.

**Costo:** ~150 llamadas API (una por moneda, cada una devuelve el rango completo). Muy razonable.

### Modo B — Acumulativo
Correr el snapshot diario y dejar que la historia se acumule. Complementa al Modo A hacia adelante.

**Implementa el Modo A como comando `backfill`.** Es la diferencia entre un proyecto con datos y uno vacío.

---

## 7. Cálculo de retornos

Para cada `tenure`, para cada hito en `[20, 50, 100, 200]`:

```python
target_date = entry_date + timedelta(days=milestone)

if target_date > today:
    skip  # aún no ha pasado ese tiempo

price_at_day = get_price(coin_id, target_date)   # con cache
return_pct = ((price_at_day - entry_price) / entry_price) * 100
```

**Requisitos:**
- Siempre consultar `price_cache` antes de llamar la API
- Los retornos ya calculados no se recalculan (son inmutables)
- Si un tenure terminó antes del hito (ej: salió del top 50 al día 30, hito d50), **igual calcular el retorno** — es información valiosa. Marcar con flag `exited_before_milestone`.

---

## 8. Categorización

Archivo `config/categories.yaml`:

```yaml
categories:
  Layer 1:
    - bitcoin
    - ethereum
    - solana
    - cardano
    - avalanche-2
    - tron
    - polkadot
    - near
    - aptos
    - sui
    - the-open-network
    - internet-computer
    - hedera-hashgraph
    - cosmos
    - algorand
    - kaspa

  Layer 2:
    - matic-network
    - arbitrum
    - optimism
    - immutable-x
    - mantle
    - starknet

  DeFi:
    - uniswap
    - aave
    - maker
    - lido-dao
    - jupiter-exchange-solana
    - ethena

  Oracle / Infra:
    - chainlink
    - the-graph
    - filecoin

  Stablecoin:
    - tether
    - usd-coin
    - dai
    - first-digital-usd
    - ethena-usde

  Meme:
    - dogecoin
    - shiba-inu
    - pepe
    - bonk
    - dogwifcoin
    - floki

  Exchange Token:
    - binancecoin
    - okb
    - crypto-com-chain
    - leo-token

  AI / DePIN:
    - render-token
    - bittensor
    - fetch-ai
    - filecoin
    - helium

  Payments / RWA:
    - ripple
    - stellar
    - ondo-finance

  Privacy:
    - monero
    - zcash

  Gaming / Metaverse:
    - the-sandbox
    - decentraland
    - immutable-x
    - gala

fallback: "Other"
```

**Requisito:** al correr el tracker, si aparece una moneda sin categoría, imprimir un warning claro listándola para que yo la agregue manualmente. No fallar silenciosamente.

---

## 9. CLI

```bash
python main.py init                      # crea DB + esquema
python main.py backfill --years 3        # reconstruye historia
python main.py snapshot                  # captura top 50 de hoy
python main.py returns                   # calcula retornos pendientes
python main.py analyze                   # genera output/analysis.json
python main.py export --format csv       # exporta tabla
python main.py run                       # snapshot + returns + analyze
python main.py status                    # resumen en terminal
```

El comando `status` debe imprimir algo así en la terminal:

```
┌─ TOP 50 CRYPTO TRACKER ─────────────────────────┐
│ Snapshots:        847 días (2023-01-15 → hoy)   │
│ Monedas trackeadas: 94                          │
│ Tenures totales:   142 (50 activos, 92 cerrados)│
│ Retornos calculados: 388 / 424                  │
├─────────────────────────────────────────────────┤
│ COMPOSICIÓN ACTUAL                              │
│   Layer 1        32%  ████████████              │
│   DeFi           16%  ██████                    │
│   Stablecoin     12%  ████                      │
│   Meme           10%  ███                       │
├─────────────────────────────────────────────────┤
│ RETORNO PROMEDIO d90 POR CATEGORÍA              │
│   AI / DePIN    +142%                           │
│   Meme           +87%                           │
│   Layer 1        +31%                           │
│   Stablecoin      +0.1%                         │
└─────────────────────────────────────────────────┘
```

---

## 10. Métricas que debe calcular `analytics.py`

### Por moneda
- Días totales en top 50 (suma de todos los tenures)
- Número de entradas (cuántas veces ha entrado)
- Duración promedio por estadía
- Rank promedio, rank mínimo (mejor), rank actual
- Retornos d20/d50/d100/d200 de cada tenure
- ¿Sigue dentro? sí/no

### Por categoría
- % del top 50 actual
- Retorno promedio y **mediana** en cada hito (la mediana importa: las medias se distorsionan con un +3000% de una meme)
- Duración promedio en top 50
- Tasa de supervivencia: % de monedas de esa categoría que sobreviven >90, >180, >365 días
- Volatilidad de retornos (desviación estándar)

### Globales
- Tasa de rotación (churn) mensual del top 50
- Distribución de duraciones (histograma)
- Correlación entre rank de entrada y supervivencia
- ¿Entrar en rank 45 vs rank 30 predice algo?

**Nota estadística importante:** siempre reportar el `n` (tamaño de muestra) junto a cada promedio. Una categoría con 3 monedas no es comparable con una de 16.

---

## 11. Dashboard

HTML estático que carga `output/analysis.json`. Sin build step, sin framework, sin servidor — se abre con doble clic.

**Vistas:**
1. **Overview** — KPIs, composición actual, retorno promedio por categoría
2. **Composición** — desglose por categoría con las monedas de cada una
3. **Retornos** — heatmap categoría × hito (d20/d50/d100/d200), con media y mediana
4. **Supervivencia** — curva estilo Kaplan-Meier: % que sigue en top 50 vs días transcurridos, una línea por categoría
5. **Monedas** — tabla filtrable y ordenable con todas las métricas
6. **Timeline** — línea de tiempo de entradas y salidas por mes

**Diseño:** oscuro, tipografía monospace para números, estética de terminal financiera. Debe verse bien en screenshot porque lo voy a publicar.

---

## 12. Criterios de aceptación

- [ ] `python main.py init && python main.py backfill --years 2` corre sin errores
- [ ] La DB contiene >500 días de snapshots después del backfill
- [ ] `analyze` produce un JSON válido con todas las métricas de la sección 10
- [ ] El dashboard abre en el navegador y renderiza todas las vistas
- [ ] Rate limiting funciona: no hay errores 429 durante un backfill completo
- [ ] Si borro `data/tracker.db` y corro `init` + `backfill`, obtengo el mismo resultado (reproducible)
- [ ] Hay tests para el cálculo de retornos y la detección de entradas/salidas
- [ ] `README.md` explica instalación y uso en menos de 20 líneas

---

## 13. Fases de construcción

Pídelas a Claude Code una por una, verificando cada una antes de seguir:

**Fase 1 — Fundación**
`db.py` (esquema + init), `coingecko.py` (cliente con rate limiting), comando `init`, `requirements.txt`.
*Verificar:* la DB se crea, una llamada de prueba a CoinGecko funciona.

**Fase 2 — Snapshots**
`tracker.py` con la lógica de entradas/salidas, comando `snapshot`, `categories.yaml`.
*Verificar:* correr `snapshot` dos veces, confirmar que no duplica tenures.

**Fase 3 — Backfill**
Reconstrucción histórica del top 50 hacia atrás.
*Verificar:* `status` muestra cientos de días de historia.

**Fase 4 — Retornos**
`returns.py` con cache de precios, comando `returns`.
*Verificar:* spot-check manual de un retorno contra CoinGecko web.

**Fase 5 — Analytics**
`analytics.py` con pandas, comando `analyze`, `status` con output bonito en terminal.
*Verificar:* el JSON tiene todas las métricas.

**Fase 6 — Dashboard**
HTML/CSS/JS.
*Verificar:* abre en navegador, todas las vistas renderizan.

**Fase 7 — Pulido**
Tests, README, `.gitignore`, manejo de errores, logging.

---

## 14. Preferencias de estilo de código

- Type hints en todas las funciones
- Docstrings cortos, en inglés
- Sin dependencias innecesarias — `requests`, `pandas`, `pyyaml` es suficiente
- Errores de API deben fallar con mensajes claros, no con stack traces crudos
- Logging con el módulo `logging`, no `print()` disperso
- Funciones pequeñas, testeables, sin efectos secundarios ocultos
- Nada de sobreingeniería: no quiero clases abstractas ni patrones de diseño donde una función basta

---

## 15. Preguntas abiertas para discutir con Claude Code

Antes de la Fase 3, plantéale esto:

1. ¿El backfill debe reconstruir el top 50 con las top 150 actuales, o hay forma de obtener el ranking histórico real? (Sesgo de supervivencia: las monedas que colapsaron y salieron del top 150 no aparecerían.)
2. ¿Cómo manejar rebrandings y migraciones de token? (MATIC → POL, LUNA → LUNC)
3. ¿Las stablecoins deben excluirse de los promedios de retorno? Su retorno es ~0% por diseño y arrastran las medias hacia abajo.
4. ¿Vale la pena guardar el precio diario de todas las monedas trackeadas, o solo en los hitos?
