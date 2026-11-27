#!/usr/bin/env python3
"""Confere e corrige o trailer de cada ficha (detalhes/*.json).

O campo "trailer" é o id de um vídeo do YouTube. Dois robôs já escreveram nele:
  * capas.yml            pega os vídeos do TMDB daquele título;
  * trailers_youtube.py  procura no YouTube pelo título (pode achar vídeo de
                         outro filme, reupload ou trailer de outra língua).

Este script confere os dois casos, sem confiar em nenhum deles:

  1. o id está na lista de vídeos do TMDB daquele título?       -> mantém
  2. o TMDB tem um trailer/teaser para o título?                -> troca por ele
  3. o título do vídeo no YouTube bate com o título da ficha?   -> mantém
  4. nada disso: o trailer está errado                          -> remove o campo

Modos:
  MODO=auditar    só relata (não grava ficha nenhuma); grava um JSON com o
                  resultado em SAIDA (padrão /tmp/trailers_auditoria.json)
  MODO=corrigir   aplica as trocas/remoções nas fichas

Variáveis:
  TMDB_API_KEY  obrigatória no modo corrigir (o CI já tem o secret)
  LIMITE        opcional: só as N primeiras fichas
  IDS           opcional: só essas fichas (separadas por vírgula)
  WORKERS       paralelismo (padrão 8)
  SAIDA         relatório do modo auditar
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
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
DETALHES = RAIZ / "detalhes"
API = "https://api.themoviedb.org/3"

MODO = (os.environ.get("MODO") or "auditar").strip().lower()
CHAVE = (os.environ.get("TMDB_API_KEY") or "").strip()
LIMITE = int(os.environ.get("LIMITE") or 0)
WORKERS = int(os.environ.get("WORKERS") or 8)
IDS = {i for i in (os.environ.get("IDS") or "").replace(" ", "").split(",") if i}
SAIDA = (os.environ.get("SAIDA") or ""
         or (str(RAIZ / ".github" / "trailers_relatorio.json") if MODO == "corrigir"
             else "/tmp/trailers_auditoria.json"))

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

RX_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
# trailer já em português do Brasil: nunca trocar por um do TMDB em inglês
PT_BR = re.compile(r"dublad|legendad|portugu[êe]s|\bbrasil\b|nacional|\bpt[-\s]?br\b", re.I)
CANAL_BR = re.compile(r"brasil|telefilms|a2 filmes|diamond|playarte|imovision|paris filmes|"
                      r"o2 play|galeria distribuidora|california filmes|europa filmes|"
                      r"pandora filmes|artefilmes|espa[çc]o filmes|elite filmes|sunset filmes|"
                      r"vitrine filmes|imagem filmes|bonfilm|vers[áa]til|cinepop|omelete|adorocinema", re.I)
# trailer de Portugal: não serve para o site brasileiro
PT_PT = re.compile(r"\bdobrad|portugal|\(pt\)", re.I)
CANAL_PT = re.compile(r"portugal|cinemas nos|nos audiovisuais", re.I)
STOP = {"the", "of", "and", "a", "an", "o", "os", "as", "de", "da", "do", "das",
        "dos", "e", "em", "um", "uma", "para", "por", "com", "que", "no", "na",
        "nos", "nas", "ao", "aos", "à", "às", "el", "la", "los", "las", "le",
        "les", "un", "une", "des", "du", "der", "die", "das", "und", "il", "lo"}


def log(msg):
    print(msg, flush=True)


def norm(txt):
    txt = unicodedata.normalize("NFKD", str(txt or ""))
    txt = "".join(c for c in txt if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", txt.lower()).strip()


def palavras(txt):
    return [p for p in norm(txt).split() if len(p) >= 3 and p not in STOP]


def titulo_bate(titulo_ficha, titulo_video):
    """O título do vídeo corresponde ao título da ficha?

    Tolerante de propósito: trailer costuma vir como "O Protetor 2 | Trailer
    Oficial Dublado". Exige que as palavras fortes do título da ficha
    apareçam no vídeo (ou o contrário, para títulos longos com sufixo).
    """
    a, b = palavras(titulo_ficha), palavras(titulo_video)
    if not a or not b:
        return None                      # sem dados: não dá para julgar
    sa, sb = set(a), set(b)
    if len(a) <= 2:
        return len(sa & sb) >= max(1, len(sa) - 1)
    inter = len(sa & sb)
    if inter / len(sa) >= 0.6:
        return True
    # título do vídeo é o da ficha + sufixo ("Deadpool 2 Trailer Final")
    if len(sb) <= len(sa) + 4 and len(sa & sb) / len(sb) >= 0.5:
        return True
    return False


def http_json(url, tentativas=3):
    for i in range(tentativas):
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as erro:
            if erro.code in (429, 500, 502, 503, 504) and i < tentativas - 1:
                time.sleep(1.5 * (i + 1))
                continue
            if erro.code in (401, 404):
                return None
            raise
        except Exception:
            if i < tentativas - 1:
                time.sleep(1.0 * (i + 1))
                continue
            return None
    return None


def tmdb_videos(kind, ident):
    """Vídeos do TMDB daquele título (pt-BR, pt e en-US), ou None se falhar."""
    if not CHAVE:
        return None
    alvo = ident
    if str(ident).lower().startswith("tt"):
        achado = http_json(f"{API}/find/{ident}?api_key={CHAVE}&external_source=imdb_id")
        lista = ((achado or {}).get("tv_results" if kind == "tv" else "movie_results") or [])
        alvo = lista[0].get("id") if lista else None
    if not alvo:
        return None
    dados = http_json(f"{API}/{kind}/{alvo}/videos?api_key={CHAVE}"
                      f"&language=pt-BR&include_video_language=pt-BR,pt,en")
    if dados is None:
        return None
    return [v for v in (dados.get("results") or [])
            if isinstance(v, dict) and (v.get("site") or "").lower() == "youtube" and v.get("key")]


def melhor_video(videos):
    """Trailer oficial em pt-BR > trailer pt > trailer > teaser; nunca 'clip'."""
    def nota(v):
        tipo = (v.get("type") or "").lower()
        if tipo not in ("trailer", "teaser"):
            return None
        idioma = str(v.get("iso_639_1") or "").lower()
        pais = str(v.get("iso_3166_1") or "").upper()
        nome = (v.get("name") or "").lower()
        # o site é Brasil: trailer de Portugal só em último caso
        pt_br = (pais == "BR" or idioma == "pt-br" or "br" in nome or "brasil" in nome) and pais != "PT"
        pt_pt = pais == "PT" or "portugal" in nome
        return ((tipo == "trailer") * 8
                + (4 if pt_br else 0)
                - (6 if pt_pt else 0)
                + bool(v.get("official")) * 2)
    pontuados = [(nota(v), v.get("key")) for v in videos or []]
    pontuados = [(n, k) for n, k in pontuados if n is not None and k]
    if not pontuados:
        return None
    pontuados.sort(key=lambda x: -x[0])
    return pontuados[0][1]


def oembed(chave_video):
    """(info, motivo): info = título/canal do vídeo; motivo None quando deu certo,
    "nao_existe" quando o vídeo saiu do ar e "bloqueado" quando o YouTube
    limitou o robô (nesse caso não dá para julgar o trailer)."""
    url = ("https://www.youtube.com/oembed?format=json&url="
           + urllib.parse.quote(f"https://www.youtube.com/watch?v={chave_video}", safe="/"))
    for tentativa in range(3):
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                dados = json.loads(r.read().decode("utf-8", "replace"))
            if not isinstance(dados, dict) or not dados.get("title"):
                return None, "nao_existe"
            return {"titulo": dados.get("title") or "", "canal": dados.get("author_name") or ""}, None
        except urllib.error.HTTPError as erro:
            if erro.code in (401, 404):
                return None, "nao_existe"
            if erro.code in (403, 429, 500, 502, 503, 504):
                if tentativa < 2:
                    time.sleep(3.0 * (tentativa + 1))
                    continue
                return None, "bloqueado"
            return None, "bloqueado"
        except Exception:
            if tentativa < 2:
                time.sleep(1.5 * (tentativa + 1))
                continue
            return None, "bloqueado"
    return None, "bloqueado"


def titulo_da_ficha(caminho):
    """Título legível da ficha: usa o cartão do catálogo; senão, o id."""
    return TITULOS.get(caminho.stem, "")


TITULOS = {}
COMPARTILHADOS = {}   # id do vídeo -> fichas que usam esse mesmo vídeo


def carregar_compartilhados(fichas):
    """Mesmo id de YouTube em fichas diferentes = pelo menos uma está errada."""
    for caminho in fichas:
        try:
            tr = (json.loads(caminho.read_text(encoding="utf-8")).get("trailer") or "").strip()
        except Exception:
            continue
        if tr:
            COMPARTILHADOS.setdefault(tr, set()).add(caminho.stem)


def carregar_titulos():
    try:
        for c in json.loads((RAIZ / "catalogo" / "cartoes.json").read_text(encoding="utf-8")):
            link = (c.get("l") or "").replace(".html", "")
            if link:
                TITULOS[link] = re.sub(r"\s*\(\d{4}\)\s*$", "", c.get("t") or "").strip()
    except Exception as erro:
        log(f"⚠️  sem catalogo/cartoes.json ({type(erro).__name__}); vou usar o id como título.")


def processar(caminho):
    """Devolve um dict com o veredito para a ficha."""
    try:
        bruto = caminho.read_text(encoding="utf-8")
        d = json.loads(bruto)
    except Exception as erro:
        return {"ficha": caminho.stem, "status": "ilegivel", "erro": str(erro)}
    atual = (d.get("trailer") or "").strip()
    titulo = titulo_da_ficha(caminho)
    if not atual:
        return {"ficha": caminho.stem, "titulo": titulo, "status": "sem_trailer", "atual": ""}
    if not RX_ID.match(atual):
        return {"ficha": caminho.stem, "titulo": titulo, "status": "formato_ruim", "atual": atual}

    kind = "tv" if caminho.stem.startswith("serie--") else "movie"
    ident = caminho.stem.replace("serie--", "")
    videos = tmdb_videos(kind, ident)

    if videos is None:
        # sem TMDB (ou o TMDB falhou) não dá para julgar com segurança
        return {"ficha": caminho.stem, "titulo": titulo, "status": "sem_tmdb", "atual": atual}

    if atual in {v.get("key") for v in videos}:
        return {"ficha": caminho.stem, "titulo": titulo, "status": "confirmado_tmdb", "atual": atual}

    novo = melhor_video(videos)
    info, motivo = oembed(atual)

    # Trailer atual em português do Brasil e que é mesmo deste título: mantém,
    # mesmo que o TMDB não liste (trailer dublado de distribuidora brasileira
    # costuma existir só no YouTube e é melhor que um trailer em inglês).
    if info and not PT_PT.search(info["titulo"]) and not CANAL_PT.search(info["canal"]):
        marca_pt = bool(PT_BR.search(info["titulo"]) or CANAL_BR.search(info["canal"]))
        # se o mesmo vídeo está em outra ficha com OUTRO título (continuação,
        # remake...), não dá para saber de quem ele é: deixa o TMDB decidir
        conflito = any(norm(TITULOS.get(outro, outro)) != norm(titulo)
                       for outro in COMPARTILHADOS.get(atual, set()) - {caminho.stem})
        if marca_pt and not conflito and titulo_bate(titulo, info["titulo"]) is True:
            return {"ficha": caminho.stem, "titulo": titulo, "status": "confirmado_pt",
                    "atual": atual, "video": info["titulo"]}

    if novo and novo != atual:
        return {"ficha": caminho.stem, "titulo": titulo, "status": "trocar", "atual": atual, "novo": novo}

    # o TMDB não tem vídeo para este título: o trailer atual não é dele
    compartilhado = COMPARTILHADOS.get(atual, set()) - {caminho.stem}
    if compartilhado:
        return {"ficha": caminho.stem, "titulo": titulo, "status": "remover", "atual": atual,
                "motivo": "video_de_outro_titulo", "tambem_em": sorted(compartilhado)[:5]}

    if info is None and motivo == "nao_existe":
        return {"ficha": caminho.stem, "titulo": titulo, "status": "remover", "atual": atual,
                "motivo": "video_indisponivel"}
    if info is None:
        return {"ficha": caminho.stem, "titulo": titulo, "status": "sem_veredito",
                "atual": atual, "motivo": "youtube_bloqueou"}
    bate = titulo_bate(titulo, info["titulo"])
    if bate:
        return {"ficha": caminho.stem, "titulo": titulo, "status": "confirmado_youtube",
                "atual": atual, "video": info["titulo"]}
    if bate is None:                      # título sem palavra comparável (ex.: devanágari)
        return {"ficha": caminho.stem, "titulo": titulo, "status": "sem_veredito",
                "atual": atual, "video": info["titulo"]}
    # não dá para provar que está errado sem o TMDB: só relata, não apaga
    return {"ficha": caminho.stem, "titulo": titulo, "status": "suspeito", "atual": atual,
            "video": info["titulo"], "canal": info["canal"], "motivo": "titulo_nao_bate"}


def aplicar(resultado):
    """Grava a troca/remoção na ficha, mexendo só no campo "trailer"."""
    if MODO != "corrigir":
        return
    status = resultado.get("status")
    if status not in ("trocar", "remover"):
        return
    caminho = DETALHES / f"{resultado['ficha']}.json"
    try:
        bruto = caminho.read_text(encoding="utf-8")
    except OSError:
        return
    if status == "trocar":
        novo = resultado["novo"]
        texto = re.sub(r'("trailer"\s*:\s*")[^"]*(")', lambda m: m.group(1) + novo + m.group(2),
                       bruto, count=1)
    else:
        texto = re.sub(r',?"trailer"\s*:\s*"[^"]*"', "", bruto, count=1)
        # se "trailer" era o primeiro campo, a vírgula sobrou no começo
        texto = re.sub(r"^\{\s*,", "{", texto)
    if texto == bruto:
        return
    try:
        json.loads(texto)                 # nunca grava JSON inválido
    except ValueError:
        resultado["status"] = "erro_json"
        return
    caminho.write_text(texto, encoding="utf-8")


def main():
    carregar_titulos()
    if MODO == "corrigir" and not CHAVE:
        log("❌ MODO=corrigir precisa do secret TMDB_API_KEY.")
        return 1

    fichas = sorted(DETALHES.glob("*.json"))
    carregar_compartilhados(fichas)
    if IDS:
        fichas = [f for f in fichas if f.stem in IDS]
    if LIMITE > 0:
        fichas = fichas[:LIMITE]
    log(f"modo {MODO}: {len(fichas)} fichas | workers: {WORKERS}"
        + (" | TMDB: sim" if CHAVE else " | TMDB: não (só YouTube)"))

    contagem, problemas = {}, []
    inicio = time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for i, resultado in enumerate(pool.map(processar, fichas), 1):
            aplicar(resultado)
            status = resultado.get("status")
            contagem[status] = contagem.get(status, 0) + 1
            if status in ("trocar", "remover", "formato_ruim", "suspeito"):
                problemas.append(resultado)
            if i % 500 == 0:
                log(f"  {i}/{len(fichas)} — {contagem} ({time.time() - inicio:.0f}s)")

    log(f"\nresumo: {contagem}")
    Path(SAIDA).write_text(json.dumps({"resumo": contagem, "problemas": problemas,
                                       "quando": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
                                      ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"relatório: {SAIDA} ({len(problemas)} fichas com problema)")
    log(f"tempo: {time.time() - inicio:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
