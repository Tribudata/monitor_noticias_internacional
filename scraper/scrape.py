#!/usr/bin/env python3
"""
Extrae los cinco titulares más recientes de tres fuentes internacionales
—The Economist, BBC Mundo y Yahoo Finanzas— y los guarda en
data/noticias.json.

Los titulares se reescriben con Gemini antes de publicarse. Los de The
Economist vienen en inglés y además se traducen al español de Colombia.
El original queda en titulo_fuente y el enlace apunta a la nota.
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# Una fila por fuente, en el orden en que se pintan.
#   idioma  "en" activa la traducción además de la reescritura.
SECCIONES = {
    "Economist": {
        # El feed RSS en vez de la portada: la web devuelve 403 a todo lo
        # que no sea un navegador, y un feed está hecho para ser leído
        # por programas.
        "url": "https://www.economist.com/finance-and-economics/rss.xml",
        "base": "https://www.economist.com/",
        "idioma": "en",
    },
    "BBC": {
        "url": "https://www.bbc.com/mundo/topics/c06gq9v4xp3t",
        "base": "https://www.bbc.com/",
        "idioma": "es",
    },
    "Yahoo": {
        "url": "https://es.finance.yahoo.com/",
        "base": "https://es.finance.yahoo.com/",
        "idioma": "es",
    },
}

POR_SECCION = 5    # titulares que se guardan y se muestran por fila
DIAS_RETENCION = 30

# --- Reescritura y traducción de titulares -------------------------------
API_MODELO = "gemini-3.5-flash"
API_URL = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{API_MODELO}:generateContent")
API_CLAVE = os.environ.get("GEMINI_API_KEY", "")
LOTE = 5         # titulares por llamada
REINTENTOS = 4   # ante 429/503, que son transitorios

REGLAS = """Reglas estrictas:
- No agregues ningún dato, cifra, nombre o matiz que no esté en el original.
- No omitas cifras, nombres propios, siglas ni instituciones.
- Nada de adjetivos valorativos, interpretaciones ni opiniones.
- Máximo 110 caracteres. Sin comillas ni punto final.

Responde ÚNICAMENTE con un arreglo JSON de cadenas, en el mismo orden y con
la misma cantidad de elementos que recibiste. Sin explicaciones ni markdown."""

INSTRUCCION_ES = """Reformula cada titular de prensa económica con palabras
distintas, conservando exactamente el mismo significado. Español de Colombia,
registro neutro de prensa económica.

""" + REGLAS

INSTRUCCION_EN = """Traduce cada titular al español de Colombia y reformúlalo
con palabras propias, conservando exactamente el mismo significado.

Sobre el idioma:
- Registro neutro de prensa económica colombiana.
- Nada de modismos de España ni de México. Nunca uses "vosotros", "coche",
  "ordenador", "ahorita" ni "platicar".
- Usa los términos de la prensa financiera colombiana: tasas de interés,
  bonos, junta directiva, mercado accionario.
- Conserva en su forma original los nombres propios, instituciones y siglas
  (Fed, BCE, FMI). No traduzcas nombres de personas ni de empresas.

""" + REGLAS

SALIDA = Path(__file__).resolve().parent.parent / "data" / "noticias.json"
BOGOTA = timezone(timedelta(hours=-5))

CABECERAS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "es-CO,es;q=0.9,en;q=0.8",
    "Accept": "application/rss+xml, application/xml, text/html;q=0.9",
}


def limpiar(texto: str) -> str:
    return re.sub(r"\s+", " ", (texto or "").replace("\xa0", " ")).strip()


def descargar(url: str) -> str:
    r = requests.get(url, headers=CABECERAS, timeout=30)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or "utf-8"
    return r.text


# --- Un extractor por fuente: cada sitio arma su portada distinto --------

def extraer_economist(html: str, base: str) -> list:
    """Lee el feed RSS: <item> con <title> y <link>, ya ordenado por fecha."""
    sopa = BeautifulSoup(html, "xml")
    items = []

    for entrada in sopa.find_all("item"):
        titulo = limpiar(entrada.title.get_text() if entrada.title else "")
        enlace = limpiar(entrada.link.get_text() if entrada.link else "")
        if not titulo or not enlace:
            continue
        fecha = entrada.find("pubDate")
        items.append({
            "titulo": titulo,
            "url": urljoin(base, enlace),
            "publicacion": limpiar(fecha.get_text()) if fecha else "",
        })

    return items


def extraer_bbc(html: str, base: str) -> list:
    """Cada promo es un <li> con el titular en h2 y la fecha en <time>."""
    sopa = BeautifulSoup(html, "lxml")
    zona = sopa.select_one('[data-testid="topic-promos"]') or sopa
    items = []

    for li in zona.select("li"):
        enlace = li.select_one("h2 a[href]")
        if not enlace:
            continue

        # En los videos el titular trae "Video," y la duración como texto
        # oculto, en dos tramos distintos: hay que quitarlos todos.
        for oculto in enlace.select('[data-testid="visually-hidden-text"]'):
            oculto.extract()

        # Quitar el texto oculto deja una coma suelta al final en los videos.
        titulo = re.sub(r"[,;\s]+$", "", limpiar(enlace.get_text()))
        if not titulo:
            continue

        fecha = li.select_one("time[datetime]")
        items.append({
            "titulo": titulo,
            "url": urljoin(base, enlace["href"]),
            "publicacion": fecha["datetime"] if fecha else "",
        })

    return items


def extraer_yahoo(html: str, base: str) -> list:
    """Bloque principal de la portada; se descartan los enlaces a cotizaciones."""
    sopa = BeautifulSoup(html, "lxml")
    zona = sopa.select_one("section.module-hero") or sopa
    items = []
    vistas = set()

    for enlace in zona.select("a[href]"):
        encabezado = enlace.select_one("h2, h3")
        if not encabezado:
            continue

        titulo = limpiar(encabezado.get_text())
        href = enlace.get("href") or ""
        if not titulo or not href or href.startswith("/quote/"):
            continue

        url = urljoin(base, href)
        if url in vistas:
            continue
        vistas.add(url)

        items.append({"titulo": titulo, "url": url})

    return items


EXTRACTORES = {
    "Economist": extraer_economist,
    "BBC": extraer_bbc,
    "Yahoo": extraer_yahoo,
}


def diagnostico(seccion: str, html: str) -> None:
    sopa = BeautifulSoup(html, "lxml")
    print(f"  {seccion}: {len(html)} bytes, {len(sopa.select('h2'))} h2, "
          f"{len(sopa.select('h3'))} h3, {len(sopa.select('a[href]'))} enlaces",
          file=sys.stderr)


def adaptar(titulares: list, idioma: str = "es") -> list:
    """Devuelve la versión propia de cada titular, o [] si no se pudo.

    Con idioma "en" el modelo traduce además de reformular. Si falla, el
    llamador conserva lo que tenía y reintenta en la siguiente corrida.
    """
    if not titulares:
        return []
    if not API_CLAVE:
        print("  falta GEMINI_API_KEY: no se adaptan titulares", file=sys.stderr)
        return []

    instruccion = INSTRUCCION_EN if idioma == "en" else INSTRUCCION_ES
    salida = []

    for i in range(0, len(titulares), LOTE):
        trozo = titulares[i:i + LOTE]
        cuerpo = {
            "system_instruction": {"parts": [{"text": instruccion}]},
            "contents": [{
                "role": "user",
                "parts": [{"text": json.dumps(trozo, ensure_ascii=False)}],
            }],
            "generationConfig": {
                "temperature": 0.4,
                "responseMimeType": "application/json",
            },
        }

        adaptados = None
        for intento in range(1, REINTENTOS + 1):
            try:
                r = requests.post(
                    API_URL,
                    headers={
                        "x-goog-api-key": API_CLAVE,
                        "content-type": "application/json",
                    },
                    json=cuerpo,
                    timeout=90,
                )
                if r.status_code == 429 or r.status_code >= 500:
                    raise requests.HTTPError(f"HTTP {r.status_code}", response=r)
                r.raise_for_status()

                partes = r.json()["candidates"][0]["content"]["parts"]
                texto = "".join(x.get("text", "") for x in partes)
                texto = re.sub(r"^```(?:json)?|```$", "", texto.strip()).strip()
                adaptados = json.loads(texto)
                break

            except requests.HTTPError as e:
                codigo = e.response.status_code if e.response is not None else 0
                if codigo != 429 and codigo < 500:
                    print(f"  no se pudieron adaptar los titulares ({e})",
                          file=sys.stderr)
                    return []
                if intento == REINTENTOS:
                    print(f"  no se pudieron adaptar los titulares tras "
                          f"{REINTENTOS} intentos ({e})", file=sys.stderr)
                    return []
                espera = 2 ** intento
                print(f"  {e}; reintento {intento}/{REINTENTOS - 1} en {espera}s",
                      file=sys.stderr)
                time.sleep(espera)

            except (requests.RequestException, json.JSONDecodeError,
                    KeyError, IndexError) as e:
                print(f"  no se pudieron adaptar los titulares ({e})",
                      file=sys.stderr)
                return []

        if adaptados is None:
            return []
        if not isinstance(adaptados, list) or len(adaptados) != len(trozo):
            print("  respuesta inesperada al adaptar titulares", file=sys.stderr)
            return []

        salida.extend(limpiar(str(a)) for a in adaptados)

    return salida


def cargar_previo() -> dict:
    if not SALIDA.exists():
        return {}
    try:
        return json.loads(SALIDA.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def fusionar(previo: dict, nuevo: dict, ahora: str) -> dict:
    secciones_previas = previo.get("secciones", {})
    limite = datetime.fromisoformat(ahora) - timedelta(days=DIAS_RETENCION)
    salida = {}
    nuevos_totales = 0

    for seccion, cfg in SECCIONES.items():
        por_url = {}

        for item in secciones_previas.get(seccion, []):
            try:
                if datetime.fromisoformat(item["capturado"]) < limite:
                    continue
            except (KeyError, ValueError):
                pass
            por_url[item["url"]] = item

        # La portada ya viene ordenada de más reciente a más antigua, así que
        # basta con tomar los primeros de la lista.
        candidatos = nuevo.get(seccion, [])[:POR_SECCION]
        pendientes = [i for i in candidatos if i["url"] not in por_url]
        adaptados = adaptar([i["titulo"] for i in pendientes], cfg["idioma"])

        if pendientes and not adaptados:
            print(f"  {seccion}: {len(pendientes)} titulares quedan para la "
                  f"próxima corrida (sin adaptación)", file=sys.stderr)

        for item, propio in zip(pendientes, adaptados):
            por_url[item["url"]] = {
                **item,
                "titulo": propio,                 # redacción propia, es la que se publica
                "titulo_fuente": item["titulo"],  # original, solo como referencia
                "capturado": ahora,
            }
            nuevos_totales += 1

        # Se conservan los cinco más recientes que sí quedaron adaptados.
        vigentes = [por_url[i["url"]] for i in candidatos if i["url"] in por_url]
        if len(vigentes) < POR_SECCION:
            resto = sorted(
                (v for v in por_url.values() if v not in vigentes),
                key=lambda i: i.get("capturado", ""),
                reverse=True,
            )
            vigentes.extend(resto[:POR_SECCION - len(vigentes)])

        salida[seccion] = vigentes[:POR_SECCION]

    return {
        "actualizado": ahora,
        "nuevos_en_esta_corrida": nuevos_totales,
        "secciones": salida,
    }


def main() -> int:
    ahora = datetime.now(BOGOTA).isoformat(timespec="seconds")
    nuevo = {}
    fallos = 0

    for seccion, cfg in SECCIONES.items():
        try:
            html = descargar(cfg["url"])
        except requests.RequestException as e:
            print(f"{seccion}: no se pudo descargar ({e})", file=sys.stderr)
            nuevo[seccion] = []
            fallos += 1
            continue

        items = EXTRACTORES[seccion](html, cfg["base"])
        if not items:
            diagnostico(seccion, html)

        nuevo[seccion] = items
        print(f"{seccion}: {len(items)} titulares en portada")

    if not any(nuevo.values()):
        print("Ninguna fuente devolvió titulares: revise el diagnóstico.",
              file=sys.stderr)
        return 1

    datos = fusionar(cargar_previo(), nuevo, ahora)
    SALIDA.parent.mkdir(parents=True, exist_ok=True)
    SALIDA.write_text(
        json.dumps(datos, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Guardado {SALIDA} · {datos['nuevos_en_esta_corrida']} titulares nuevos")
    return 1 if fallos == len(SECCIONES) else 0


if __name__ == "__main__":
    raise SystemExit(main())
