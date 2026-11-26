#!/usr/bin/env python3
"""Acha trailer em português no YouTube para fichas que continuaram em inglês.

Estratégia: busca no YouTube (sem API key, lendo os resultados da página) e
pontua cada resultado — só aceita trailer com marca de português (dublado /
legendado / português / brasil), canal oficial ou agregador de trailers,
título batendo com o do catálogo e ano compatível. Nada de "filme completo",
react, review, teoria, fancut.

Modos (variáveis de ambiente):
  AMOSTRA=n    só mostra o que faria, com n fichas (não grava nada)
  LIMITE=n     aplica nas n primeiras fichas elegíveis
  IDS=a,b,c    restringe a essas fichas
  IGNORAR=arq  arquivo com ids de ficha a pular (uma por linha)
  WORKERS=3    paralelismo (padrão 3)
  SAIDA=arq    onde salvar o relatório JSON da amostra
"""

import json
import os
import random
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
CATALOGO = os.path.join(RAIZ, "catalogo")
ESTADO = os.path.join(RAIZ, ".github", "trailers_pt_estado.json")

AMOSTRA = int(os.environ.get("AMOSTRA") or 0)
LIMITE = int(os.environ.get("LIMITE") or 0)
WORKERS = int(os.environ.get("WORKERS") or 3)
IDS = [i for i in (os.environ.get("IDS") or "").replace(" ", "").split(",") if i]
SAIDA = os.environ.get("SAIDA") or ""

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

RX_TRAILER = re.compile(r'("trailer"\s*:\s*")[^"]*(")')
RX_ANO = re.compile(r'\b(19\d{2}|20\d{2})\b')

RUIM = re.compile(
    r'\breact\b|\brea[çc][ãa]o\b|reagindo|\breview\b|\ban[áa]lise\b|\bteoria\b|'
    r'concept\s*trailer|fan\s*-?\s*made|\bfã\s*-?\s*trailer|par[óo]dia|parody|'
    r'filme\s+completo|\bfilmes?\s+online|\bcena\b|\bcenas\b|\bclipe\b|karaok|'
    r'gameplay|trailer\s+do\s+jogo|\bjogo\b|\bwhere\s+to\s+watch\b'
)
PT_FORTE = re.compile(r'\bdublad[oa]\b|\bdublagem\b|\bdublado\s+em\s+portugu', re.I)
PT_MEDIO = re.compile(r'\blegendad[oa]\b', re.I)
PT_MARCA = re.compile(r'portugu[eê]s|\bpt[-\s]?br\b|\bbrasil\b|\bnacional\b', re.I)

# Canal de distribuidora/estúdio/streaming ou veículo de cinema: só nesses
# confiamos em aplicar sozinho (reupload aleatório pode ser dublagem de fã,
# qualidade baixa ou vídeo nada a ver).
DISTRIBUIDORAS = (
    "warner", "sony pictures", "sony pictures brasil", "universal pictures",
    "universal pictures brasil", "disney", "walt disney", "pixar", "marvel",
    "lucasfilm", "paramount", "20th century", "searchlight", "lionsgate", "a24",
    "netflix", "prime video", "amazon mgm", "amazon prime", "apple tv",
    "globoplay", "hbo", "max brasil", "star+", "disney+", "telecine",
    "imagem filmes", "diamond films", "diamond distribuidora", "paris filmes",
    "galeria distribuidora", "o2 play", "playarte", "synapse", "california filmes",
    "europa filmes", "imovision", "pandora filmes", "a2 filmes", "artefilmes",
    "espa[çc]o filmes", "elite filmes", "sunset filmes", "vitrine filmes",
    "msm studios", "bonfilm", "versátil", "versatil home video", "cinesystem",
)
VEICULOS = ("telefilms", "portal trailers", "cinepop", "omelete", "adorocinema",
            "ingresso.com", "pipoca moderna", "cinem(a|a) com pipoca",
            "trailer oficial", "trailers oficiais")
# português de Portugal ("dobrado") não serve: o site é Brasil
PT_PORTUGAL = re.compile(r'\bdobrad[oa]\b|\bdobragem\b|\bportugal\b|\(pt\)', re.I)
MARCA_BR = re.compile(r'\bdublad[oa]\b|\bbrasil\b|pt[-\s]?br|\(br\)', re.I)

SUSPEITO = re.compile(r'leil[ãa]o|leilao|clipes?|karaok|m[úu]sica|gameplay|'
                      r'filmes?\s+completos?|novelas?\b|receitas?\b', re.I)

STOP = {"the", "of", "and", "a", "o", "os", "as", "de", "da", "do", "das", "dos",
        "e", "em", "um", "uma", "para", "por", "com", "que", "the"}


def log(msg):
    print(msg, flush=True)


def norm(txt):
    txt = unicodedata.normalize("NFKD", txt or "")
    txt = "".join(c for c in txt if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", txt.lower()).strip()


def pegar_html(url, tentativas=3):
    for i in range(tentativas):
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.6",
        })
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                return r.read().decode("utf-8", "replace")
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
    return ""


def duracao_seg(txt):
    if not txt:
        return 0
    partes = [p for p in re.split(r":", txt) if p.strip().isdigit()]
    total = 0
    for p in partes:
        total = total * 60 + int(p)
    return total


def buscar_youtube(consulta, quantos=20):
    """Resultados da busca do YouTube (videoId, título, canal, duração)."""
    url = "https://www.youtube.com/results?" + urllib.parse.urlencode(
        {"search_query": consulta, "hl": "pt-BR", "gl": "BR"})
    html = pegar_html(url)
    m = re.search(r'ytInitialData\s*=\s*(\{.*?\});\s*</script>', html, re.S)
    if not m:
        return []
    try:
        dados = json.loads(m.group(1))
    except Exception:
        return []
    achados = []

    def anda(x):
        if isinstance(x, dict):
            if "videoRenderer" in x:
                vr = x["videoRenderer"]
                vid = vr.get("videoId")
                titulo = "".join(r.get("text", "") for r in (vr.get("title") or {}).get("runs", []))
                canal = "".join(r.get("text", "") for r in (vr.get("ownerText") or {}).get("runs", []))
                dur = (vr.get("lengthText") or {}).get("simpleText", "")
                if vid and titulo:
                    achados.append({
                        "id": vid, "titulo": titulo, "canal": canal,
                        "dur": duracao_seg(dur), "dur_txt": dur,
                    })
            for v in x.values():
                anda(v)
        elif isinstance(x, list):
            for v in x:
                anda(v)

    anda(dados)
    return achados[:quantos]


def oembed(vid):
    """Título e canal reais do vídeo (confere também se ele ainda existe)."""
    url = ("https://www.youtube.com/oembed?" +
           urllib.parse.urlencode({"url": f"https://youtu.be/{vid}", "format": "json"}))
    try:
        dados = json.loads(pegar_html(url, tentativas=2))
        return dados.get("title") or "", dados.get("author_name") or ""
    except Exception:
        return None, None


def nivel_canal(canal):
    """'A' = distribuidora/veículo conhecido, 'B' = canal qualquer."""
    c = norm(canal)
    if SUSPEITO.search(canal):
        return None
    if any(re.search(d, c) for d in DISTRIBUIDORAS):
        return "A"
    if any(re.search(v, c) for v in VEICULOS):
        return "A"
    return "B"


def pontuar(v, titulo, ano, tokens):
    t = norm(v["titulo"])
    c = norm(v["canal"])
    if "trailer" not in t and "trailer" not in c:
        return None
    if RUIM.search(v["titulo"].lower()):
        return None
    if PT_PORTUGAL.search(v["titulo"]) and not MARCA_BR.search(v["titulo"]):
        return None
    if not (PT_FORTE.search(t) or PT_MEDIO.search(t) or PT_MARCA.search(t)):
        return None
    # o título do filme tem que aparecer inteiro no título do vídeo
    # (evita "Os Fora da Lei" casar com "Paixão Fora da Lei")
    titulo_norm = norm(titulo)
    if titulo_norm and titulo_norm not in t:
        return None
    cobertura = 1.0 if titulo_norm else 0
    # ano: se o vídeo cita um ano, tem que bater
    anos = [int(a) for a in RX_ANO.findall(v["titulo"])]
    if anos and ano:
        if min(abs(a - ano) for a in anos) > 1:
            return None
    # duração de trailer
    dur = v["dur"]
    if dur and not (40 <= dur <= 300):
        return None

    p = 0.0
    if PT_FORTE.search(t):
        p += 6
    if PT_MEDIO.search(t):
        p += 4
    if PT_MARCA.search(t):
        p += 2
    if "trailer oficial" in t or "trailer" in c:
        p += 2
    if "oficial" in t:
        p += 1
    if any(o in c for o in DISTRIBUIDORAS):
        p += 3
    elif any(o in c for o in VEICULOS):
        p += 2
    if cobertura >= 0.99:
        p += 3
    elif cobertura >= 0.8:
        p += 1
    if dur and 60 <= dur <= 200:
        p += 1
    if "dublado" in c:
        p += 1
    return p


def achar(titulo, ano):
    """Melhor candidato em português para o título, já confirmado via oEmbed."""
    base = re.sub(r'\s*\(\d{4}\)\s*$', "", titulo).strip().strip('"').strip("“”")
    tokens = [t for t in norm(base).split() if len(t) > 2 and t not in STOP]
    consultas = []
    if ano:
        consultas.append(f"{base} {ano} trailer dublado")
    consultas.append(f"{base} trailer dublado")
    consultas.append(f"{base} trailer legendado português")
    consultas.append(f"{base} trailer oficial legendado")
    vistos, candidatos = set(), []
    for q in consultas:
        try:
            for v in buscar_youtube(q):
                if v["id"] in vistos:
                    continue
                vistos.add(v["id"])
                p = pontuar(v, base, ano, tokens)
                if p is not None:
                    candidatos.append((p, v))
        except Exception as e:
            log(f"    ! busca falhou ({q}): {type(e).__name__}")
        time.sleep(0.5)
    if not candidatos:
        return None
    candidatos.sort(key=lambda x: -x[0])
    for p, v in candidatos[:4]:
        tit, can = oembed(v["id"])
        if not tit:
            continue
        v2 = dict(v, titulo=tit, canal=can)
        p2 = pontuar(v2, base, ano, tokens)
        nivel = nivel_canal(can or "")
        if nivel and p2 is not None and p2 >= 9:
            v2["nota"] = round(p2, 1)
            v2["nivel"] = nivel
            return v2
    return None


def carregar_titulos():
    """id da ficha (sem .html) -> (título, ano)."""
    mapa = {}
    try:
        for c in json.load(open(os.path.join(CATALOGO, "cartoes.json"), encoding="utf-8")):
            link = (c.get("l") or "").replace(".html", "")
            if link and c.get("t"):
                mapa[link] = (c["t"], c.get("a"), c.get("n"))
    except Exception as e:
        log(f"! cartoes.json: {e}")
    return mapa


def ler_ficha(caminho):
    bruto = open(caminho, encoding="utf-8").read()
    try:
        dados = json.loads(bruto)
    except Exception:
        dados = {}
    return bruto, dados


def elegiveis(mapa, ignorar):
    saida = []
    for nome in sorted(os.listdir(DETALHES)):
        if not nome.endswith(".json"):
            continue
        ident = nome[:-5]
        if ident in ignorar or nome in ignorar:
            continue
        chave = ident if ident in mapa else (ident.replace("serie--", "", 1))
        if chave not in mapa:
            continue
        bruto, dados = ler_ficha(os.path.join(DETALHES, nome))
        if not (dados.get("trailer") or "").strip():
            continue
        titulo, ano, nota = mapa[chave]
        saida.append({"id": ident, "arquivo": nome, "titulo": titulo,
                      "ano": ano or dados.get("ano"), "nota": nota or 0,
                      "trailer": dados["trailer"]})
    return saida


def main():
    mapa = carregar_titulos()
    ignorar = set()
    arq_ignorar = os.environ.get("IGNORAR")
    if arq_ignorar and os.path.exists(arq_ignorar):
        ignorar = {l.strip().replace(".json", "") for l in open(arq_ignorar, encoding="utf-8") if l.strip()}
    lista = elegiveis(mapa, ignorar)
    log(f"fichas com trailer e título conhecido: {len(lista)} (ignoradas: {len(ignorar)})")

    if IDS:
        alvo = [x for x in lista if x["id"] in IDS]
    elif AMOSTRA:
        random.seed(7)
        # amostra variada: títulos conhecidos (nota >= 6.5), filmes e séries
        conhecidos = [x for x in lista if (x.get("nota") or 0) >= 6.5]
        if os.environ.get("FILTRO") == "recentes":
            conhecidos = [x for x in conhecidos
                          if (x.get("ano") or 0) >= 2015 and (x.get("nota") or 0) >= 7]
        random.shuffle(conhecidos)
        alvo = conhecidos[: AMOSTRA * 2]
    else:
        random.seed(7)
        random.shuffle(lista)
        alvo = lista[: LIMITE] if LIMITE else lista

    log(f"a analisar: {len(alvo) if not AMOSTRA else min(AMOSTRA, len(alvo))}")

    relatorio = []
    achados = 0

    def tarefa(item):
        try:
            cand = achar(item["titulo"], item["ano"])
        except Exception as e:
            log(f"  ! {item['id']}: {type(e).__name__} {e}")
            return None
        if not cand:
            return dict(item, achou=None)
        return dict(item, achou=cand)

    inicio = time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for i, res in enumerate(pool.map(tarefa, alvo), 1):
            if res is None:
                continue
            relatorio.append(res)
            if res.get("achou"):
                achados += 1
            if AMOSTRA and len([r for r in relatorio]) >= AMOSTRA:
                break
            if i % 25 == 0:
                log(f"  {i} analisadas | {achados} com trailer pt | {time.time() - inicio:.0f}s")

    if AMOSTRA:
        achados_r = [r for r in relatorio if r.get("achou")]
        print("\n" + "=" * 100)
        for r in achados_r[:AMOSTRA]:
            atual_t, atual_c = oembed(r["trailer"])
            print(f"\n{r['titulo']}  ({r['ano']})   [ficha {r['id']}]")
            print(f"  AGORA : {r['trailer']} | {atual_c or '?'} | {atual_t or '?'}")
            if r.get("achou"):
                a = r["achou"]
                marca = "✅" if a.get("nivel") == "A" else "⚠️ revisar"
                print(f"  {marca} {a['id']} | {a['canal']} | {a['titulo']}")
                print(f"          duração {a['dur_txt']} | nota {a['nota']}")
            else:
                print("  PT-BR : (nada bom encontrado)")
        if len(achados_r) < AMOSTRA:
            print("\n--- casos sem candidato bom (ficariam em inglês) ---")
            for r in [x for x in relatorio if not x.get("achou")][: AMOSTRA - len(achados_r)]:
                at, ac = oembed(r["trailer"])
                print(f"  {r['titulo']} ({r['ano']}) [ficha {r['id']}] — agora {r['trailer']} | {ac or '?'} | {at or '?'}")
        print("\n" + "=" * 100)
        log(f"amostra: {len(achados_r)} com trailer pt-BR de {len(relatorio)} analisadas")
        if SAIDA:
            json.dump(relatorio, open(SAIDA, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            log(f"relatório salvo em {SAIDA}")
        return 0

    # modo aplicação
    estado = {}
    if os.path.exists(ESTADO):
        try:
            estado = json.load(open(ESTADO, encoding="utf-8"))
        except Exception:
            estado = {}
    trocados = 0
    for r in relatorio:
        a = r.get("achou")
        if not a:
            estado[r["id"]] = {"fonte": "youtube", "resultado": "nao_encontrado"}
            continue
        if a.get("nivel") != "A":
            estado[r["id"]] = {"fonte": "youtube", "resultado": "revisar",
                               "video": a["id"], "canal": a["canal"], "titulo": a["titulo"]}
            continue
        caminho = os.path.join(DETALHES, r["arquivo"])
        try:
            bruto = open(caminho, encoding="utf-8").read()
            novo = RX_TRAILER.sub(lambda m: m.group(1) + a["id"] + m.group(2), bruto, count=1)
            if novo != bruto:
                open(caminho, "w", encoding="utf-8").write(novo)
                trocados += 1
                estado[r["id"]] = {"fonte": "youtube", "resultado": "trocado",
                                   "video": a["id"], "canal": a["canal"], "titulo": a["titulo"]}
        except Exception as e:
            log(f"  ! gravar {r['arquivo']}: {e}")
    os.makedirs(os.path.dirname(ESTADO), exist_ok=True)
    json.dump(estado, open(ESTADO, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    log(f"trocados: {trocados} | analisadas: {len(relatorio)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
