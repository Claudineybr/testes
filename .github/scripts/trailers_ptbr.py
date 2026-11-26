#!/usr/bin/env python3
"""Troca o trailer das fichas (detalhes/*.json) pela versão em português (pt-BR).

Como funciona:
  * cada ficha tem "trailer": "<id do YouTube>";
  * o script pergunta ao TMDB os vídeos daquele título em pt-BR/pt;
  * se existir trailer/teaser em português, o id é trocado; se não existir,
    a ficha fica como está (melhor o trailer em inglês do que nenhum).

O arquivo é alterado apenas no valor do campo "trailer" — o resto do JSON
continua byte a byte igual, então o diff é de uma linha por ficha.

Variáveis:
  TMDB_API_KEY  obrigatória (secret do repositório)
  LIMITE        opcional: processa só as N primeiras fichas (teste)
  WORKERS       opcional: paralelismo (padrão 8)
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
DETALHES = RAIZ / "detalhes"
API = "https://api.themoviedb.org/3"
CHAVE = (os.environ.get("TMDB_API_KEY") or "").strip()
LIMITE = int(os.environ.get("LIMITE") or 0)
WORKERS = int(os.environ.get("WORKERS") or 8)
RX_TRAILER = re.compile(r'("trailer"\s*:\s*")[^"]*(")')
RX_ISO = {"pt", "pt-br", "pt_br"}


def log(msg):
    print(msg, flush=True)


def pegar(caminho, params):
    """GET no TMDB com 2 tentativas extras em 429/5xx."""
    params = dict(params)
    params["api_key"] = CHAVE
    url = f"{API}{caminho}?" + urllib.parse.urlencode(params)
    for tentativa in range(3):
        req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "clau-flix-trailers"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as erro:
            if erro.code in (429, 500, 502, 503, 504) and tentativa < 2:
                time.sleep(1.5 * (tentativa + 1))
                continue
            raise
        except Exception:
            if tentativa < 2:
                time.sleep(1.0 * (tentativa + 1))
                continue
            raise
    return None


def id_tmdb(kind, ident):
    """Converte o id do arquivo (numérico ou tt...) no id do TMDB."""
    if not ident.lower().startswith("tt"):
        return int(ident)
    achado = pegar(f"/find/{ident}", {"external_source": "imdb_id"})
    lista = (achado or {}).get("tv_results" if kind == "tv" else "movie_results") or []
    if not lista:
        return None
    return lista[0].get("id")


def melhor_pt(videos):
    """Escolhe o melhor vídeo em português: trailer oficial > trailer > teaser > outro."""
    candidatos = []
    for v in videos or []:
        if not isinstance(v, dict):
            continue
        if (v.get("site") or "").lower() != "youtube":
            continue
        if str(v.get("iso_639_1") or "").lower() not in RX_ISO:
            continue
        chave = v.get("key") or ""
        if not chave:
            continue
        tipo = (v.get("type") or "").lower()
        peso = 3 if tipo == "trailer" else 2 if tipo == "teaser" else 1
        if v.get("official"):
            peso += 0.5
        candidatos.append((peso, chave))
    if not candidatos:
        return None
    candidatos.sort(key=lambda x: -x[0])
    return candidatos[0][1]


def processar(caminho):
    """Retorna (status, antigo, novo). status: 'trocado' | 'sem_pt' | 'igual' | 'sem_id' | 'erro'."""
    nome = caminho.name
    kind = "tv" if nome.startswith("serie--") else "movie"
    ident = nome[:-5].replace("serie--", "")
    try:
        bruto = caminho.read_text(encoding="utf-8")
        atual = (json.loads(bruto).get("trailer") or "").strip()
    except Exception:
        atual = ""
    if not atual:
        return ("sem_trailer", atual, atual)
    try:
        alvo = id_tmdb(kind, ident)
        if not alvo:
            return ("sem_id", atual, atual)
        dados = pegar(f"/{kind}/{alvo}/videos", {"language": "pt-BR", "include_video_language": "pt-BR,pt"})
        novo = melhor_pt((dados or {}).get("results"))
    except Exception as erro:
        print(f"  ! {nome}: {type(erro).__name__} {erro}", flush=True)
        return ("erro", atual, atual)
    if not novo:
        return ("sem_pt", atual, atual)
    if novo == atual:
        return ("igual", atual, atual)
    try:
        texto = RX_TRAILER.sub(lambda m: m.group(1) + novo + m.group(2), bruto, count=1)
        if texto == bruto:
            return ("erro", atual, atual)
        caminho.write_text(texto, encoding="utf-8")
    except Exception as erro:
        print(f"  ! {nome}: {type(erro).__name__} {erro}", flush=True)
        return ("erro", atual, atual)
    return ("trocado", atual, novo)


def main():
    if not CHAVE:
        log("❌ Secret TMDB_API_KEY não encontrado (crie uma chave grátis em themoviedb.org > Configurações > API).")
        return 1

    fichas = sorted(DETALHES.glob("*.json"))
    if LIMITE > 0:
        fichas = [f for f in fichas if '"trailer"' in f.read_text(encoding="utf-8", errors="ignore")][:LIMITE]
    log(f"fichas a analisar: {len(fichas)} | workers: {WORKERS}")

    contagem = {}
    trocas = []
    inicio = time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for i, (status, antigo, novo) in enumerate(pool.map(processar, fichas), 1):
            contagem[status] = contagem.get(status, 0) + 1
            if status == "trocado":
                trocas.append((antigo, novo))
            if i % 500 == 0:
                log(f"  {i}/{len(fichas)} — {contagem} ({time.time() - inicio:.0f}s)")

    log(f"\nresumo: {contagem}")
    log(f"trailers trocados para pt-BR: {len(trocas)}")
    if trocas[:5]:
        log("exemplos: " + ", ".join(f"{a} -> {b}" for a, b in trocas[:5]))
    log(f"tempo: {time.time() - inicio:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
