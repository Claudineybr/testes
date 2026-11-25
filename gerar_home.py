#!/usr/bin/env python3
"""Gera os arquivos leves que o site baixa (tudo dentro de catalogo/):

  home.json    ~12 KB  destaques + especial de Natal + contagem de gêneros/décadas
  cartoes.json ~3 MB   um cartão por título (nota e gêneros embutidos), para
                       Filmes, Séries, busca e Minha Lista
  videos.json  ~1,6 MB link do filme -> URL do vídeo, só quando for tocar

Antes, exibir as abas custava catalogo/parte-*.json (37 MB) + classificacao
(19 MB); agora custa só o cartoes.json.

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


def limpar_generos(lista):
    if not isinstance(lista, list):
        return []
    return [g for g in lista if g and g != GENERO_INDEFINIDO]


def generos_da_serie(eps):
    cont = {}
    for ep in eps:
        for g in ep["generos"]:
            cont[g] = cont.get(g, 0) + 1
    ordenado = sorted(cont.items(), key=lambda kv: (-kv[1], normalizar(kv[0])))
    return [g for g, _ in ordenado[:MAX_GENEROS_HERO]]


def chave(item):
    return "serie:" + item["titulo"] if item["isAgrupado"] else item["link"]


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

    notas = carregar(NOTAS, {})
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

    kb = lambda p: os.path.getsize(p) / 1024
    print(f"{SAIDA}: {kb(SAIDA):.1f} KB — {len(titulos)} títulos "
          f"({len(filmes)} filmes, {len(series)} séries), {len(destaques)} destaques, "
          f"{len(natal)} no especial de Natal, {len(generos)} gêneros, {len(decadas)} décadas")
    print(f"{SAIDA_CARTOES}: {kb(SAIDA_CARTOES):.1f} KB — {len(cartoes)} cartões")
    print(f"{SAIDA_VIDEOS}: {kb(SAIDA_VIDEOS):.1f} KB — {len(videos)} filmes com vídeo")


if __name__ == "__main__":
    main()
