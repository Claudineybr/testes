#!/usr/bin/env python3
"""Troca sinopses em inglês por português nas fichas de detalhes/.

Ordem de preferência:
  1. TMDB em pt-BR (precisa de TMDB_API_KEY);
  2. TMDB em pt-PT (o site é Brasil, mas é melhor que inglês);
  3. tradução automática do texto em inglês (Google, com MyMemory de reserva).

A ficha que passa por tradução automática ganha "sinopseTraduzida": true, para
poder ser substituída depois por uma sinopse oficial do TMDB. A flag
"sinopseEn" é removida quando a sinopse deixa de estar em inglês.

Variáveis de ambiente:
  TMDB_API_KEY  chave v3 do TMDB (opcional; sem ela só traduz)
  LIMITE        processa no máximo N fichas (0/vazio = todas)
  WORKERS       paralelismo (padrão 4)
  IDS           lista de ids separada por vírgula (teste)
"""

import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DETALHES = os.path.join(RAIZ, "detalhes")
CAPAS = os.path.join(RAIZ, "capas.json")

CHAVE = (os.environ.get("TMDB_API_KEY") or "").strip()
LIMITE = int(os.environ.get("LIMITE") or 0)
WORKERS = int(os.environ.get("WORKERS") or 4)
IDS = {i for i in (os.environ.get("IDS") or "").replace(" ", "").split(",") if i}

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36"

EN = set(
    "the and of to in a is are was were with for on that this from by as at his her "
    "their it its an or be been has have had not but they you we he she who what when "
    "where while after before into out up down about over under his him them our your".split()
)
PT = set(
    "de da do das dos e em um uma para por com que não se ao aos sua seu suas seus foi "
    "ser mais como mas pelo pela nos nas dos uma the".split()
)


def log(msg):
    print(msg, flush=True)


def norm(txt):
    txt = unicodedata.normalize("NFKD", txt or "")
    txt = "".join(c for c in txt if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", txt.lower()).strip()


def parece_ingles(texto):
    """Heurística conservadora: só marca como inglês o que é claramente inglês."""
    if not texto:
        return False
    palavras = re.findall(r"[A-Za-zÀ-ÿ']+", texto.lower())
    if len(palavras) < 4:
        return False
    en = sum(1 for p in palavras if p in EN)
    pt = sum(1 for p in palavras if p in PT)
    acentos = len(re.findall(r"[ãõçáéíóúâêôà]", texto, re.I))
    if acentos >= 2:
        return False
    return en >= 3 and en > pt * 2


def http_json(url, tentativas=3):
    for i in range(tentativas):
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "pt-BR,pt;q=0.9"})
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and i < tentativas - 1:
                time.sleep(2.0 * (i + 1))
                continue
            raise
        except Exception:
            if i < tentativas - 1:
                time.sleep(1.5 * (i + 1))
                continue
            raise
    return None


def tmdb_overview(tipo, tid, idioma):
    if not CHAVE or not tid:
        return ""
    url = ("https://api.themoviedb.org/3/%s/%s?" % (tipo, tid) +
           urllib.parse.urlencode({"api_key": CHAVE, "language": idioma}))
    try:
        d = http_json(url) or {}
    except Exception:
        return ""
    return " ".join((d.get("overview") or "").split())


def _pedacos(texto, limite=450):
    """Divide em pedaços de até `limite` caracteres, cortando em fim de frase/espaço."""
    resto = texto.strip()
    while resto:
        if len(resto) <= limite:
            yield resto
            return
        corte = max(resto.rfind(". ", 0, limite), resto.rfind("! ", 0, limite),
                    resto.rfind("? ", 0, limite), resto.rfind("; ", 0, limite))
        if corte < limite // 2:
            corte = resto.rfind(" ", 0, limite)
        if corte <= 0:
            corte = limite
        yield resto[:corte + 1].strip()
        resto = resto[corte + 1:].strip()


def traduzir_google(texto):
    partes = []
    for pedaco in _pedacos(texto, 1500):
        url = "https://translate.googleapis.com/translate_a/single?" + urllib.parse.urlencode(
            {"client": "gtx", "sl": "en", "tl": "pt", "dt": "t", "q": pedaco})
        d = http_json(url, tentativas=2)
        partes.append("".join(p[0] for p in (d or [[[]]])[0] if p and p[0]))
        time.sleep(0.2)
    saida = " ".join(partes).strip()
    if not saida or parece_ingles(saida):
        raise ValueError("tradução vazia/ruim")
    return saida


def traduzir_mymemory(texto):
    partes = []
    for pedaco in _pedacos(texto, 450):
        url = "https://api.mymemory.translated.net/get?" + urllib.parse.urlencode(
            {"q": pedaco, "langpair": "en|pt-BR"})
        d = http_json(url, tentativas=2) or {}
        t = ((d.get("responseData") or {}).get("translatedText") or "").strip()
        if not t or parece_ingles(t):
            raise ValueError("MyMemory sem resposta útil")
        partes.append(t)
        time.sleep(0.3)
    return " ".join(partes).strip()


def traduzir(texto):
    for funcao in (traduzir_google, traduzir_mymemory):
        try:
            t = funcao(texto)
            if t:
                return t
        except Exception as e:
            log(f"    ! {funcao.__name__}: {type(e).__name__}")
    return ""


def carregar_mapa():
    """id da ficha -> (tipo no TMDB, id no TMDB)."""
    try:
        capas = json.load(open(CAPAS, encoding="utf-8"))
    except Exception as e:
        log(f"! capas.json: {e}")
        capas = {}
    mapa = {}
    for nome in os.listdir(DETALHES):
        if not nome.endswith(".json"):
            continue
        stem = nome[:-5]
        if stem.startswith("serie--"):
            tid = stem[len("serie--"):]
            e = capas.get("serie:" + tid) or {}
            mapa[stem] = ("tv", e.get("tmdb_id") or int(tid))
        else:
            e = capas.get(stem) or {}
            if e.get("tmdb_id"):
                mapa[stem] = ("movie", e["tmdb_id"])
    return mapa


def processar(item, tipo, tid, caminho):
    bruto = open(caminho, encoding="utf-8").read()
    try:
        d = json.loads(bruto)
    except Exception:
        return None
    sinopse = (d.get("sinopse") or "").strip()
    if not sinopse:
        return None
    if not (d.get("sinopseEn") or parece_ingles(sinopse)):
        return None

    novo = tmdb_overview(tipo, tid, "pt-BR") or tmdb_overview(tipo, tid, "pt-PT")
    traduzida = False
    if not novo:
        novo = traduzir(sinopse)
        traduzida = True
    if not novo:
        return ("falhou", item, sinopse[:60])

    d["sinopse"] = " ".join(novo.split())
    d.pop("sinopseEn", None)
    if traduzida:
        d["sinopseTraduzida"] = True
    else:
        d.pop("sinopseTraduzida", None)
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, separators=(",", ":"))
    return ("traduzida" if traduzida else "tmdb", item, d["sinopse"][:60])


def main():
    mapa = carregar_mapa()
    alvos = []
    for nome in sorted(os.listdir(DETALHES)):
        if not nome.endswith(".json"):
            continue
        stem = nome[:-5]
        if IDS and stem not in IDS:
            continue
        caminho = os.path.join(DETALHES, nome)
        try:
            d = json.loads(open(caminho, encoding="utf-8").read())
        except Exception:
            continue
        s = (d.get("sinopse") or "").strip()
        if not s:
            continue
        if not (d.get("sinopseEn") or parece_ingles(s)):
            continue
        tipo, tid = mapa.get(stem, ("movie", None))
        alvos.append((stem, tipo, tid, caminho))

    log(f"fichas com sinopse em inglês: {len(alvos)} | chave TMDB: {'sim' if CHAVE else 'não'}")
    if LIMITE:
        alvos = alvos[:LIMITE]
        log(f"processando só {len(alvos)} (LIMITE={LIMITE})")

    conta = {"tmdb": 0, "traduzida": 0, "falhou": 0}
    inicio = time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for i, res in enumerate(pool.map(lambda a: processar(*a), alvos), 1):
            if res is None:
                continue
            tipo_res, item, amostra = res
            conta[tipo_res] = conta.get(tipo_res, 0) + 1
            if tipo_res == "falhou":
                log(f"  ! {item}: sem tradução")
            if i % 25 == 0:
                log(f"  {i}/{len(alvos)} | {conta} | {time.time() - inicio:.0f}s")

    log(f"resumo: {conta} | {time.time() - inicio:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
