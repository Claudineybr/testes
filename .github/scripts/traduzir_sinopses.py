#!/usr/bin/env python3
"""Deixa em português do Brasil os textos das fichas de detalhes/.

Campos tratados: "sinopse" (overview) e "frase" (tagline). Ordem:
  1. TMDB em pt-BR (precisa de TMDB_API_KEY);
  2. TMDB em pt-PT;
  3. tradução automática do inglês (Google com destino pt-BR, MyMemory de reserva),
     passada por um glossário PT-PT -> PT-BR e por uma limpeza de artefatos.

Modos (variáveis de ambiente):
  (padrão)      corrige fichas com sinopse/frase em inglês
  RETRADUZIR=1  refaz a tradução das fichas marcadas "sinopseTraduzida" a partir
                do texto original em inglês do TMDB (en-US), com o pipeline novo
  LIMPAR=1      só passa glossário/limpeza no texto já traduzido (sem rede)
  LIMITE=n      no máximo n fichas (0/vazio = todas)
  WORKERS=n     paralelismo (padrão 4)
  IDS=a,b,c     restringe a essas fichas
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
RETRADUZIR = os.environ.get("RETRADUZIR") == "1"
SO_LIMPAR = os.environ.get("LIMPAR") == "1"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

EN = set(
    "the and of to in a is are was were with for on that this from by as at his her "
    "their it its an or be been has have had not but they you we he she who what when "
    "where while after before into out up down about over under his him them our your".split()
)
PT = set(
    "de da do das dos e em um uma para por com que não se ao aos sua seu suas seus foi "
    "ser mais como mas pelo pela nos nas".split()
)

# PT-PT -> PT-BR (o tradutor devolve português de Portugal em muitos casos).
GLOSSARIO = [
    (r"\bequipas\b", "equipes"), (r"\bequipa\b", "equipe"),
    (r"\bactivistas\b", "ativistas"), (r"\bactivista\b", "ativista"),
    (r"\bactivos\b", "ativos"), (r"\bactivo\b", "ativo"),
    (r"\bactivas\b", "ativas"), (r"\bactiva\b", "ativa"),
    (r"\bactivar\b", "ativar"), (r"\bactividade\b", "atividade"),
    (r"\becrãs\b", "telas"), (r"\becrã\b", "tela"),
    (r"\btelemóveis\b", "celulares"), (r"\btelemóvel\b", "celular"),
    (r"\bcasa de banho\b", "banheiro"),
    (r"\bautocarros\b", "ônibus"), (r"\bautocarro\b", "ônibus"),
    (r"\butilizadores\b", "usuários"), (r"\butilizador\b", "usuário"),
    (r"\bfrigorífico\b", "geladeira"), (r"\bfrigoríficos\b", "geladeiras"),
    (r"\braparigas\b", "garotas"), (r"\brapariga\b", "garota"),
    (r"\bmiúdos\b", "garotos"), (r"\bmiúdo\b", "garoto"),
    (r"\bcamiões\b", "caminhões"), (r"\bcamião\b", "caminhão"),
    (r"\bobjectos\b", "objetos"), (r"\bobjecto\b", "objeto"),
    (r"\bactriz\b", "atriz"), (r"\bactrizes\b", "atrizes"),
    (r"\bfacto\b", "fato"), (r"\bfactos\b", "fatos"),
    (r"\bdirector\b", "diretor"), (r"\bdirectores\b", "diretores"),
    (r"\bdirectora\b", "diretora"),
    (r"\bconnosco\b", "conosco"),
    (r"\bportagem\b", "pedágio"), (r"\bportagens\b", "pedágios"),
    (r"\beletricista\b", "eletricista"),
    (r"\bactuando\b", "atuando"), (r"\bactuar\b", "atuar"),
    (r"\bcontactados\b", "contatados"), (r"\bcontactado\b", "contatado"),
    (r"\bcontactada\b", "contatada"), (r"\bcontactar\b", "contatar"),
    (r"\bcontacto\b", "contato"), (r"\bcontactos\b", "contatos"),
    (r"\bperspetivas\b", "perspectivas"), (r"\bperspetiva\b", "perspectiva"),
    (r"\brealizadores\b", "diretores"), (r"\brealizador\b", "diretor"),
    (r"\brealizadora\b", "diretora"),
    (r"\bargumentistas\b", "roteiristas"), (r"\bargumentista\b", "roteirista"),
    (r"\bguionista\b", "roteirista"), (r"\bguionistas\b", "roteiristas"),
    (r"\bbilheteiras\b", "bilheterias"), (r"\bbilheteira\b", "bilheteria"),
    (r"\bcomboios\b", "trens"), (r"\bcomboio\b", "trem"),
    (r"\bbebés\b", "bebês"), (r"\bbebé\b", "bebê"),
    (r"\bténis\b", "tênis"),
    (r"\breceção\b", "recepção"), (r"\breceções\b", "recepções"),
    (r"\bgolos\b", "gols"), (r"\bgolo\b", "gol"),
    (r"\bsandes\b", "sanduíches"),
    (r"\besquentador\b", "aquecedor"),
    (r"\bcarta de condução\b", "carteira de motorista"),
    (r"\bcoima\b", "multa"), (r"\bcoimas\b", "multas"),
    (r"\bautarquia\b", "prefeitura"),
    (r"\bpercurso\b", "trajeto"),
]
_GLOSS = [(re.compile(p, re.I), s) for p, s in GLOSSARIO]

# "está a fazer" -> "está fazendo" (progressivo típico de Portugal).
RX_PROGRESSIVO = re.compile(
    r"\b(está|estão|estava|estavam|esteve|estiveram|estando)\s+a\s+([a-záàâãéêíóôõúç]+"
    r"(?:ar|er|ir))\b", re.I)
IRREGULARES = {
    "fazer": "fazendo", "dizer": "dizendo", "trazer": "trazendo", "vir": "vindo",
    "ir": "indo", "ler": "lendo", "ver": "vendo", "pôr": "pondo", "ter": "tendo",
    "ser": "sendo", "sentir": "sentindo", "dormir": "dormindo", "pedir": "pedindo",
    "subir": "subindo", "ouvir": "ouvindo", "sair": "saindo", "cair": "caindo",
    "crer": "crendo", "medir": "medindo", "perder": "perdendo", "poder": "podendo",
}


def log(msg):
    print(msg, flush=True)


def norm(txt):
    txt = unicodedata.normalize("NFKD", txt or "")
    txt = "".join(c for c in txt if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", txt.lower()).strip()


def gerundio(verbo):
    v = verbo.lower()
    if v in IRREGULARES:
        return IRREGULARES[v]
    if v.endswith("ar"):
        return v[:-2] + "ando"
    if v.endswith("er"):
        return v[:-2] + "endo"
    if v.endswith("ir"):
        return v[:-2] + "indo"
    return v


def parece_ingles(texto):
    """Heurística conservadora: só marca como inglês o que é claramente inglês."""
    if not texto:
        return False
    palavras = re.findall(r"[A-Za-zÀ-ÿ']+", texto.lower())
    if len(palavras) < 4:
        return False
    en = sum(1 for p in palavras if p in EN and len(p) > 1)
    pt = sum(1 for p in palavras if p in PT)
    acentos = len(re.findall(r"[ãõçáéíóúâêôà]", texto, re.I))
    if acentos >= 2:
        return False
    return en >= 3 and en > pt * 2


# palavras em que a repetição é proposital ("mais e mais", "dia após dia")
PALAVRAS_REPETIVEIS = {
    "mais", "menos", "muito", "muita", "pouco", "tão", "nunca", "sempre", "bem",
    "mal", "já", "não", "sim", "nada", "tudo", "cada", "todo", "toda", "vez",
    "dia", "noite", "ano", "mundo", "vida",
}


def limpar(texto, glossario=True):
    """Limpeza de artefatos de tradução (espaços, pontuação, repetição, PT-PT)."""
    s = (texto or "").replace("\u00a0", " ")
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\s+([,.;:!?])", r"\1", s)
    s = re.sub(r"([,;:])(?=\S)", r"\1 ", s)
    s = re.sub(r"\.{3,}", "...", s)
    s = re.sub(r"!{2,}", "!", s)
    s = re.sub(r"\?{2,}", "?", s)
    # palavra repetida ("idiotas idiotas", "mesquinho e mesquinho")
    # (só em minúsculas: "Sing Sing" e "Pago Pago" são nomes próprios)
    s = re.sub(r"\b(\w{3,})(?:\s+e)?\s+\1\b",
               lambda m: m.group(1)
               if m.group(0) == m.group(0).lower() and m.group(1).lower() not in PALAVRAS_REPETIVEIS
               else m.group(0),
               s, flags=re.I)
    # frases adjacentes idênticas
    partes = re.split(r"(?<=[.!?])\s+", s)
    saida = []
    for p in partes:
        if saida and p.strip().lower() == saida[-1].strip().lower():
            continue
        saida.append(p.strip())
    s = " ".join(x for x in saida if x)
    if glossario:
        for rx, sub in _GLOSS:
            s = rx.sub(sub, s)
        s = RX_PROGRESSIVO.sub(lambda m: f"{m.group(1)} {gerundio(m.group(2))}", s)
    s = re.sub(r"\s+", " ", s).strip()
    if s and s[0].islower():
        s = s[0].upper() + s[1:]
    # sinopse longa que termina sem ponto final
    if len(s) > 120 and re.search(r"[a-záéíóúãõç0-9]$", s, re.I):
        s += "."
    return s


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


def tmdb_campo(tipo, tid, idioma, campo):
    if not CHAVE or not tid:
        return ""
    url = ("https://api.themoviedb.org/3/%s/%s?" % (tipo, tid) +
           urllib.parse.urlencode({"api_key": CHAVE, "language": idioma}))
    try:
        d = http_json(url) or {}
    except Exception:
        return ""
    return " ".join((d.get(campo) or "").split())


def _pedacos(texto, limite=450):
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
            {"client": "gtx", "sl": "en", "tl": "pt-BR", "dt": "t", "q": pedaco})
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
                return limpar(t, glossario=True)
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


def escrever(caminho, d):
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, separators=(",", ":"))


def resolver_campo(d, campo, tipo, tid, titulo_base):
    """Devolve (novo_texto, origem) para sinopse/frase, ou (None, None)."""
    atual = (d.get(campo) or "").strip()
    if not atual:
        return None, None
    if campo == "frase" and norm(atual) == norm(titulo_base):
        return None, "titulo"          # tagline é só o título original: descarta
    novo = tmdb_campo(tipo, tid, "pt-BR", "tagline" if campo == "frase" else "overview")
    if not novo:
        novo = tmdb_campo(tipo, tid, "pt-PT", "tagline" if campo == "frase" else "overview")
    if novo:
        return limpar(novo, glossario=False), "tmdb"
    novo = traduzir(atual)
    if novo:
        return novo, "traducao"
    return None, None


def processar(item, tipo, tid, caminho, titulo_base):
    d = json.loads(open(caminho, encoding="utf-8").read())
    mudou = False
    origens = []

    # --- modo LIMPAR: só passa glossário/limpeza no que já é tradução ---
    if SO_LIMPAR:
        if not d.get("sinopseTraduzida"):
            return None
        for campo in ("sinopse", "frase"):
            atual = (d.get(campo) or "").strip()
            if not atual:
                continue
            novo = limpar(atual, glossario=True)
            if novo and novo != atual:
                d[campo] = novo
                mudou = True
        if not mudou:
            return None
        escrever(caminho, d)
        return ("limpo", item, "")

    # --- modo RETRADUZIR: parte do original em inglês do TMDB ---
    if RETRADUZIR:
        if not d.get("sinopseTraduzida"):
            return None
        ingles = tmdb_campo(tipo, tid, "en-US", "overview") or d.get("sinopseOriginal") or ""
        if not ingles:
            return ("sem_original", item, "")
        novo = traduzir(ingles) or limpar(d.get("sinopse") or "", glossario=True)
        if not novo:
            return ("falhou", item, "")
        d["sinopse"] = novo
        d["sinopseOriginal"] = " ".join(ingles.split())
        if d.get("frase"):
            frase = limpar(d["frase"], glossario=True)
            if frase:
                d["frase"] = frase
        escrever(caminho, d)
        return ("retraduzida", item, novo[:60])

    # --- modo padrão: campos ainda em inglês ---
    for campo in ("sinopse", "frase"):
        atual = (d.get(campo) or "").strip()
        if not atual:
            continue
        # tagline que é só o título do filme não é texto traduzível: descarta
        if campo == "frase" and norm(atual) == norm(titulo_base):
            d.pop(campo, None)
            mudou = True
            origens.append("titulo")
            continue
        if not parece_ingles(atual):
            continue
        novo, origem = resolver_campo(d, campo, tipo, tid, titulo_base)
        if origem == "titulo":
            d.pop(campo, None)
            mudou = True
            origens.append("titulo")
            continue
        if not novo:
            origens.append(f"falhou:{campo}")
            continue
        if campo == "sinopse" and origem == "traducao":
            d["sinopseOriginal"] = atual
            d["sinopseTraduzida"] = True
        if campo == "sinopse" and origem == "tmdb":
            d.pop("sinopseTraduzida", None)
            d.pop("sinopseOriginal", None)
        d[campo] = novo
        mudou = True
        origens.append(f"{campo}:{origem}")

    if not mudou:
        return None
    d.pop("sinopseEn", None)
    escrever(caminho, d)
    return ("corrigida", item, ",".join(origens))


def main():
    mapa = carregar_mapa()
    titulos = {}
    try:
        for c in json.load(open(os.path.join(RAIZ, "catalogo", "cartoes.json"), encoding="utf-8")):
            link = (c.get("l") or "").replace(".html", "")
            if link:
                titulos[link] = re.sub(r"\s*\(\d{4}\)\s*$", "", c.get("t") or "").strip()
    except Exception:
        pass

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
        if SO_LIMPAR or RETRADUZIR:
            if not d.get("sinopseTraduzida"):
                continue
        else:
            tem_en = any(parece_ingles(d.get(c) or "") for c in ("sinopse", "frase"))
            # tagline igual ao título não parece inglês, mas também não é texto nosso
            titulo_da_ficha = titulos.get(stem, "")
            if not tem_en and titulo_da_ficha and norm(d.get("frase") or "") == norm(titulo_da_ficha):
                tem_en = True
            if not tem_en:
                continue
        tipo, tid = mapa.get(stem, ("movie", None))
        alvos.append((stem, tipo, tid, caminho, titulos.get(stem, "")))

    modo = "LIMPAR" if SO_LIMPAR else ("RETRADUZIR" if RETRADUZIR else "corrigir")
    log(f"modo {modo}: {len(alvos)} fichas | chave TMDB: {'sim' if CHAVE else 'não'}")
    if LIMITE:
        alvos = alvos[:LIMITE]
        log(f"processando só {len(alvos)} (LIMITE={LIMITE})")

    conta = {}
    inicio = time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for i, res in enumerate(pool.map(lambda a: processar(*a), alvos), 1):
            if res is None:
                continue
            tipo_res = res[0]
            conta[tipo_res] = conta.get(tipo_res, 0) + 1
            if tipo_res in ("falhou", "sem_original"):
                log(f"  ! {res[1]}: {tipo_res}")
            if i % 50 == 0:
                log(f"  {i}/{len(alvos)} | {conta} | {time.time() - inicio:.0f}s")

    log(f"resumo: {conta} | {time.time() - inicio:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
