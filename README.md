# Crypto Top 50 Quant Tracker
Rastrea el top 50 cripto por market cap: permanencia, retornos post-entrada (d20/50/100/200) y performance por categoría.

**Instalación:**
```
pip install -r requirements.txt
cp .env.example .env   # opcional: CG_API_KEY gratis de coingecko.com/en/developers/dashboard
```
**Uso:**
```
python main.py init && python main.py backfill --years 1   # reconstruye historia (~364 dias, ver nota)
python main.py run                                          # snapshot + returns + analyze (uso diario)
python main.py status                                       # resumen en terminal
```
Abrí `dashboard/index.html` con doble clic (no necesita servidor ni internet).

**Nota:** el free tier de CoinGecko limita histórico a ~365 días (aunque tengas API key demo); `--years 3` se clampea solo. Corré `snapshot` seguido para acumular más historia.

`config/categories.yaml`: categorías editables. `config/seed_extra_coins.yaml`: monedas muertas/deslistadas a forzar en el backfill (sesgo de supervivencia, ver comentario ahí).

Tests: `pip install pytest && pytest tests/ -v`
