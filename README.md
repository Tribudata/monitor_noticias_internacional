# Monitor internacional — Economist, BBC y Yahoo

Recoge cada dos horas los cinco titulares más recientes de tres fuentes
internacionales, los reescribe con Gemini —traduciendo los de The Economist,
que vienen en inglés— y los publica en una rejilla de tres filas.

```
.github/workflows/actualizar-noticias.yml   cron + commit automático
scraper/scrape.py                           extracción, adaptación y fusión
data/noticias.json                          archivo que consume la página
index.html                                  rejilla (GitHub Pages)
requirements.txt
```

Fuentes, una por fila y en este orden:

| Fila | Origen |
|---|---|
| Economist | economist.com/topics/finance-and-economics |
| BBC | bbc.com/mundo/topics/c06gq9v4xp3t |
| Yahoo | es.finance.yahoo.com |

## Montaje

1. Repositorio `monitor_noticias_internacional`, rama `main`, con estos
   archivos en estas rutas.
2. Secreto `GEMINI_API_KEY` en Settings → Secrets and variables → Actions.
3. **Settings → Actions → General**: *Read and write permissions*.
4. **Settings → Pages**: rama `main`, carpeta `/ (root)`.
5. **Actions → Actualizar internacional → Run workflow**.

## La rejilla

- Tres filas, una por fuente, sin nombrarla. Cinco titulares por fila.
- Todas las celdas de una fila comparten alto; el titular se corta a tres
  líneas para que una nota larga no infle la fila entera.
- Cinco columnas en escritorio, dos por debajo de 900 px, una por debajo
  de 600 px.
- La marca "Nuevo" aparece durante 12 horas, no 6: estas fuentes publican
  con menos frecuencia que las colombianas.

## Extracción

Cada sitio tiene su propio extractor, porque arman la portada distinto:

- **Economist**: `a[data-testid="teaser-card-link"]`, más estable que las
  clases con hash que genera su compilador de CSS.
- **BBC**: cada promo es un `li` con el titular en `h2 a` y la fecha en
  `time[datetime]`. En los videos el titular trae "Video," y la duración
  como texto oculto para lectores de pantalla; se eliminan ambos tramos.
- **Yahoo**: bloque `section.module-hero`, descartando los enlaces a
  cotizaciones (`/quote/`) y deduplicando por URL.

Las tres portadas ya vienen ordenadas de más reciente a más antigua, así que
se toman los cinco primeros. Si una corrida no logra adaptar un titular, se
conservan los que ya estaban y se reintenta después.

## Traducción

Los titulares de The Economist se traducen y reescriben en un solo paso, con
instrucciones de español de Colombia, sin modismos de España ni de México, y
conservando nombres propios y siglas. El titular en inglés queda guardado en
`titulo_fuente` para poder auditar la traducción.

Se usa `gemini-3.5-flash` y no la versión Lite: traducir exige más que
reformular.

## Prueba local

```bash
pip install -r requirements.txt
python scraper/scrape.py
python -m http.server 8000
```
