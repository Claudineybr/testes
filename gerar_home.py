#!/usr/bin/env python3
"""Gera os arquivos leves que o site baixa (tudo dentro de catalogo/):

  home.json    ~12 KB  destaques + especial de Natal + contagem de gêneros/décadas
  cartoes.json ~3 MB   um cartão por título (nota e gêneros embutidos), para
                       Filmes, Séries, busca e Minha Lista
  videos.json  ~1,6 MB link do filme -> URL do vídeo, só quando for tocar
  eps-00..15.json      16 pedaços com episódios + filme, para a página da
                       série/filme baixar só o pedaço do título aberto

Antes, exibir as abas custava catalogo/parte-*.json (37 MB) + classificacao
(19 MB); agora custa só o cartoes.json. A ficha do título custava os mesmos
37 MB + 19 MB; agora custa ~80 KB (um pedaço de eps-*.json).

As regras abaixo são as mesmas do index.html (isSerie, nomeDaSerie, extrairAno,
generosDaSerie, chaveDetalhe, contarGeneros), para que o card não mude quando o
catálogo completo chegar.

Uso: python3 gerar_home.py
"""
import datetime
import hashlib
import json
import os
import re
import unicodedata
import urllib.parse

PASTA = "catalogo"
SAIDA = os.path.join(PASTA, "home.json")
SAIDA_CARTOES = os.path.join(PASTA, "cartoes.json")
SAIDA_VIDEOS = os.path.join(PASTA, "videos.json")
NOTAS = os.path.join(PASTA, "notas.json")
CLASSIFICACAO = "classificacao_1.json"

# Mesmo número do CONFIG.EPS_SHARDS do serie.html: mudar aqui exige mudar lá.
SHARDS = 16
SAIDA_EPS = os.path.join(PASTA, "eps-{:02d}.json")

QTD_DESTAQUES = 20
QTD_NATAL = 20
MAX_GENEROS_HERO = 3
GENERO_INDEFINIDO = "indefinido"

PADRAO_LINK = re.compile(r"(_t\d+e(?:p)?\d+|_(\d+)x(\d+)|^(?:\d+|tt\d+)s\d+e\d+)", re.I)
PADRAO_TITULO = re.compile(r"(T\d+E\d+|S\d+E\d+|\d+x\d+|\sE\d+$)", re.I)
RX_EP = re.compile(r"^(.+?)_(?:t(\d+)e(?:p)?(\d+)|(\d+)x(\d+))(?:_\d+)?$", re.I)
RX_ID_EP = re.compile(r"^(\d+|tt\d+)s\d+e\d+$", re.I)
RX_ANO_TITULO = re.compile(r"\(((?:19|20)\d{2})\)")
RX_ANO_LINK = re.compile(r"[_\- ]((?:19|20)\d{2})(?:\.html?)?$")
RX_NATAL = re.compile(
    r"\b(natal|natalin[oa]s?|christmas|xmas|noel|grinch|navidad|expresso polar|polar express|santa claus)\b"
)


def normalizar(s):
    s = unicodedata.normalize("NFD", str(s or ""))
    return "".join(c for c in s if unicodedata.category(c) != "Mn").lower()


def arquivo_do_link(link):
    base = urllib.parse.unquote(str(link or "").split("#")[0].split("?")[0]).split("/")[-1]
    return base.lower()


def is_serie(item, entrada):
    if PADRAO_LINK.search(item.get("link", "")):
        return True
    if not PADRAO_TITULO.search(item.get("titulo", "")):
        return False
    return not (entrada and entrada.get("tipo") == "filme")


def nome_da_serie(item):
    nome = (item.get("serie") or "").strip()
    if nome:
        return nome
    return re.sub(r"\s*-\s*(T\d+E\d+|\d+x\d+|\sE\d+$).*", "", item.get("titulo", ""), flags=re.I).strip()


def extrair_ano(item):
    m = RX_ANO_TITULO.search(item.get("titulo", "")) or RX_ANO_LINK.search(arquivo_do_link(item.get("link")))
    return int(m.group(1)) if m else 0


def chave_detalhe(item):
    base = re.sub(r"\.html?$", "", arquivo_do_link(item.get("link")))
    m = RX_ID_EP.match(base)
    if m:
        return "serie--" + m.group(1)
    m = RX_EP.match(base)
    if not m:
        return base
    nome = m.group(1).replace(":", "--")
    return "serie--" + re.sub(r"[^a-z0-9_.\-]", "_", nome)


GENERO_PT = {"Reality": "Reality Show"}


def limpar_generos(lista):
    if not isinstance(lista, list):
        return []
    return [GENERO_PT.get(g, g) for g in lista if g and g != GENERO_INDEFINIDO]


def generos_da_serie(eps):
    cont = {}
    for ep in eps:
        for g in ep["generos"]:
            cont[g] = cont.get(g, 0) + 1
    ordenado = sorted(cont.items(), key=lambda kv: (-kv[1], normalizar(kv[0])))
    return [g for g, _ in ordenado[:MAX_GENEROS_HERO]]


def chave(item):
    return "serie:" + item["titulo"] if item["isAgrupado"] else item["link"]


def fnv1a(texto):
    """Mesmo hash do serie.html: chave do título -> número do pedaço."""
    h = 0x811C9DC5
    for b in texto.encode("utf-8"):
        h = ((h ^ b) * 0x01000193) & 0xFFFFFFFF
    return h


def chave_serie(nome):
    return "s:" + normalizar(nome).strip()


def chave_filme(link):
    return "f:" + arquivo_do_link(link)


def gerar_episodios(series, filmes):
    """Um pedaço por faixa de hash: a página baixa só o do título aberto.

    Série: {t: nome, c: capa, a: ano, n: nota, g: gêneros, caps: {temporada: capa},
            e: [[temporada, episódio, link, vídeo], ...]}
    Filme: {t: título, c: capa, a: ano, n: nota, g: gêneros, l: link, v: vídeo}
    """
    baldes = [{"v": 2, "s": {}, "f": {}} for _ in range(SHARDS)]

    for nome, eps in series.items():
        eps = sorted(eps, key=lambda e: (int(e.get("temporada") or 1),
                                        int(e.get("episodio") or 1), str(e.get("link") or "")))
        capa = (eps[0].get("thumbnailUrl") or "").strip()
        caps = {}
        for temp in sorted({int(e.get("temporada") or 1) for e in eps}):
            achou = next(((e.get("thumbnailUrl") or "").strip() for e in eps
                          if int(e.get("temporada") or 1) == temp
                          and (e.get("thumbnailUrl") or "").strip()), "")
            if achou and achou != capa:      # capa própria da temporada (o resto usa a da série)
                caps[str(temp)] = achou
        anos = [e["ano"] for e in eps if e["ano"]]
        nota = next((e["nota"] for e in eps if e["nota"]), 0.0)
        dados = {
            "t": nome,
            "c": capa,
            "a": min(anos) if anos else 0,
            "n": round(nota, 1),
            "g": generos_da_serie(eps),
            "e": [[int(e.get("temporada") or 1), int(e.get("episodio") or 1),
                   e.get("link", ""), (e.get("videoUrl") or "").strip()] for e in eps],
        }
        if caps:
            dados["caps"] = caps
        baldes[fnv1a(chave_serie(nome)) % SHARDS]["s"][chave_serie(nome)] = dados

    for filme in filmes:
        link = filme.get("link") or ""
        if not link:
            continue
        chave = chave_filme(link)
        baldes[fnv1a(chave) % SHARDS]["f"][chave] = {
            "t": filme.get("titulo", ""),
            "c": (filme.get("thumbnailUrl") or "").strip(),
            "a": filme["ano"],
            "n": round(filme["nota"], 1),
            "g": filme["generos"],
            "l": link,
            "v": (filme.get("videoUrl") or "").strip(),
        }

    return baldes


def escrever_notas():
    """Regera catalogo/notas.json a partir de detalhes/*.json (a nota verdadeira).

    O catalogo.yml apaga e recria a pasta catalogo/ inteira; se as notas não
    fossem geradas aqui, o arquivo gravado pelo capas.yml desaparecia e todos
    os cartões ficavam sem nota.
    """
    notas = {}
    for nome in sorted(os.listdir("detalhes")):
        if not nome.endswith(".json"):
            continue
        try:
            with open(os.path.join("detalhes", nome), encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        n = d.get("nota")
        if isinstance(n, (int, float)) and not isinstance(n, bool) and n > 0:
            notas[nome[:-5]] = round(float(n), 1)
    os.makedirs(PASTA, exist_ok=True)
    with open(NOTAS, "w", encoding="utf-8") as f:
        json.dump(notas, f, ensure_ascii=False, separators=(",", ":"))
    print(f"{NOTAS}: {len(notas)} títulos com nota")
    return notas


def carregar(caminho, padrao):
    if not os.path.exists(caminho):
        print(f"⚠️  {caminho} não encontrado — seguindo sem ele.")
        return padrao
    with open(caminho, encoding="utf-8") as f:
        return json.load(f)


def main():
    brutos = []
    for nome in sorted(os.listdir(PASTA)):
        if re.fullmatch(r"parte-\d+\.json", nome):
            with open(os.path.join(PASTA, nome), encoding="utf-8") as f:
                brutos.extend(json.load(f))

    notas = escrever_notas()
    classificacao = carregar(CLASSIFICACAO, {})

    por_arquivo = {str(k).lower(): v for k, v in classificacao.items() if isinstance(v, dict)}
    por_titulo = {}
    for entrada in por_arquivo.values():
        t = normalizar(entrada.get("titulo"))
        if t and t not in por_titulo:
            por_titulo[t] = entrada

    def entrada_de(item):
        return por_arquivo.get(arquivo_do_link(item.get("link"))) or por_titulo.get(normalizar(item.get("titulo")))

    filmes, episodios = [], []
    for bruto in brutos:
        item = dict(bruto)
        entrada = entrada_de(item)
        item["eh_serie"] = is_serie(item, entrada)
        item["generos"] = limpar_generos(entrada.get("generos")) if entrada else []
        item["ano"] = extrair_ano(item)
        item["nota"] = float(notas.get(chave_detalhe(item)) or 0)
        (episodios if item["eh_serie"] else filmes).append(item)

    series = {}
    for ep in episodios:
        nome = nome_da_serie(ep)
        series.setdefault(nome, []).append(ep)

    titulos = []
    for filme in filmes:
        titulos.append({
            "titulo": filme.get("titulo", ""),
            "link": filme.get("link", ""),
            "thumbnailUrl": filme.get("thumbnailUrl", ""),
            "videoUrl": filme.get("videoUrl", ""),
            "isAgrupado": False,
            "generos": filme["generos"],
            "ano": filme["ano"],
            "nota": round(filme["nota"], 1),
        })

    for nome, eps in series.items():
        nota = 0.0
        for ep in eps:
            if ep["nota"]:
                nota = ep["nota"]
                break
        anos = [ep["ano"] for ep in eps if ep["ano"]]
        titulos.append({
            "titulo": nome,
            "serie": nome,
            "link": "serie.html?nome=" + urllib.parse.quote(nome),
            "thumbnailUrl": eps[0].get("thumbnailUrl", ""),
            "isAgrupado": True,
            "generos": generos_da_serie(eps),
            "ano": min(anos) if anos else 0,
            "nota": round(nota, 1),
        })

    titulos = [t for t in titulos if t["titulo"] and t["link"] and t["thumbnailUrl"]]
    titulos.sort(key=lambda t: (normalizar(t["titulo"]), t["link"]))

    hoje = datetime.date.today().isoformat()

    def sorteio(item):
        return hashlib.sha1((hoje + "|" + chave(item)).encode("utf-8")).hexdigest()

    destaques = sorted(titulos, key=sorteio)[:QTD_DESTAQUES]
    natal = [t for t in titulos if RX_NATAL.search(normalizar(t["titulo"]))][:QTD_NATAL]

    generos = {}
    for t in titulos:
        for g in t["generos"]:
            generos[g] = generos.get(g, 0) + 1
    decadas = sorted({(t["ano"] // 10) * 10 for t in titulos if t["ano"]}, reverse=True)

    dados = {
        "versao": 1,
        "gerado": hoje,
        "total": len(titulos),
        "generos": dict(sorted(generos.items(), key=lambda kv: (-kv[1], normalizar(kv[0])))),
        "decadas": decadas,
        "destaques": destaques,
        "natal": natal,
        "recentes": [],  # o site não tem seção de adicionados recentemente (não existe data de inclusão nos dados)
    }

    os.makedirs(PASTA, exist_ok=True)
    with open(SAIDA, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, separators=(",", ":"))

    # Índice leve usado pelas abas, busca e Minha Lista (um cartão por título, séries agrupadas).
    cartoes = [{
        "t": t["titulo"],
        "l": t["link"],
        "u": t["thumbnailUrl"],
        "a": t["ano"],
        "n": t["nota"],
        "g": t["generos"],
        "s": 1 if t["isAgrupado"] else 0,
    } for t in titulos]
    with open(SAIDA_CARTOES, "w", encoding="utf-8") as f:
        json.dump(cartoes, f, ensure_ascii=False, separators=(",", ":"))

    # Vídeo do filme só é preciso na hora de tocar: arquivo separado para não pesar ao navegar.
    videos = {}
    for filme in filmes:
        url = (filme.get("videoUrl") or "").strip()
        if url and filme.get("link"):
            videos[filme["link"]] = url
    with open(SAIDA_VIDEOS, "w", encoding="utf-8") as f:
        json.dump(videos, f, ensure_ascii=False, separators=(",", ":"))

    # Episódios e filmes em 16 pedaços: é o único arquivo que a ficha do título baixa.
    baldes = gerar_episodios(series, filmes)
    bytes_eps = 0
    for i, balde in enumerate(baldes):
        caminho = SAIDA_EPS.format(i)
        with open(caminho, "w", encoding="utf-8") as f:
            json.dump(balde, f, ensure_ascii=False, separators=(",", ":"))
        bytes_eps += os.path.getsize(caminho)

    kb = lambda p: os.path.getsize(p) / 1024
    print(f"{SAIDA}: {kb(SAIDA):.1f} KB — {len(titulos)} títulos "
          f"({len(filmes)} filmes, {len(series)} séries), {len(destaques)} destaques, "
          f"{len(natal)} no especial de Natal, {len(generos)} gêneros, {len(decadas)} décadas")
    print(f"{SAIDA_CARTOES}: {kb(SAIDA_CARTOES):.1f} KB — {len(cartoes)} cartões")
    print(f"{SAIDA_VIDEOS}: {kb(SAIDA_VIDEOS):.1f} KB — {len(videos)} filmes com vídeo")
    print(f"{SAIDA_EPS.format(0)} … {SHARDS} pedaços: {bytes_eps / 1024:.0f} KB no total "
          f"({len(series)} séries, {len(filmes)} filmes; o maior pedaço: "
          f"{max(os.path.getsize(SAIDA_EPS.format(i)) for i in range(SHARDS)) / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
