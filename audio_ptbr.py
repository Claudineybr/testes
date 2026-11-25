#!/usr/bin/env python3
"""Descobre o idioma do áudio de cada título do catálogo sem baixar o vídeo.

O catálogo do site não guarda idioma nenhum (nem as fichas, nem os .html de
origem). Quem sabe é o próprio arquivo .mp4: cada faixa tem um `mdhd` com o
idioma em ISO-639-2 (`por`, `eng`, `und` = não identificado). Este script lê só
os cabeçalhos das caixas MP4, pulando as tabelas gigantes de amostras: ~12 KB
por título em vez de centenas de MB.

Saída: artifacts/audio_ptbr.json
  {"gerado": "...", "resumo": {...},
   "titulos": [{"t","tipo","link","url","audios":["por","eng"],"status":"pt|original|indefinido|erro"}]}

Uso:
  python3 audio_ptbr.py                 # varre tudo (retoma de onde parou)
  python3 audio_ptbr.py --limite 50     # só os 50 pendentes (teste)
  python3 audio_ptbr.py --paralelo 32   # mais conexões ao mesmo tempo
"""
import argparse
import datetime
import http.client
import json
import os
import struct
import sys
import threading
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

PASTA = "catalogo"
SAIDA = os.path.join("artifacts", "audio_ptbr.json")
JANELA = 2048               # cabeçalho de uma caixa + tkhd/mdia/mdhd de uma faixa
INICIAL = 8192              # ftyp + moov + mvhd + a primeira faixa
MAX_FAIXAS = 8
TIMEOUT = 25
TENTATIVAS = 2
PARALELO = 96
POR_HOST = 8                # os servidores de vídeo engasgam com muitas conexões ao mesmo tempo
AGENTE = "Mozilla/5.0 (compatible; ClauFlix/1.0)"

# Uma conexão por host por thread: sem isso cada pedaço pagava um handshake novo
# (era o que estourava o tempo nos hosts mais lentos).
_local = threading.local()
_sems = {}
_sems_lock = threading.Lock()


def conexao(esquema, host):
    conns = getattr(_local, "conns", None)
    if conns is None:
        conns = _local.conns = {}
    chave = (esquema, host)
    if chave not in conns:
        cls = http.client.HTTPSConnection if esquema == "https" else http.client.HTTPConnection
        conns[chave] = cls(host, timeout=TIMEOUT)
    return conns[chave]


def vaga(host):
    """Semáforo por host: paralelismo global alto, mas sem sufocar um servidor só."""
    with _sems_lock:
        if host not in _sems:
            _sems[host] = threading.Semaphore(POR_HOST)
        return _sems[host]


def descartar(esquema, host):
    conns = getattr(_local, "conns", None) or {}
    c = conns.pop((esquema, host), None)
    if c:
        try:
            c.close()
        except Exception:
            pass


def ler(url, ini, fim):
    """Pedaço do arquivo (HTTP Range). Devolve (bytes, tamanho total)."""
    partes = urllib.parse.urlsplit(url)
    esquema, host = partes.scheme, partes.netloc
    caminho = partes.path + (("?" + partes.query) if partes.query else "")
    for tentativa in range(TENTATIVAS):
        with vaga(host):
            return _ler_com_conexao(url, esquema, host, caminho, ini, fim, tentativa)
    raise IOError("sem resposta")


def _ler_com_conexao(url, esquema, host, caminho, ini, fim, tentativa):
    for resto in range(TENTATIVAS - tentativa):
        try:
            c = conexao(esquema, host)
            c.request("GET", caminho, headers={"Range": f"bytes={ini}-{fim}", "User-Agent": AGENTE})
            r = c.getresponse()
            corpo = r.read()
            if r.status in (301, 302, 303, 307, 308):
                destino = r.getheader("Location") or ""
                descartar(esquema, host)
                if destino:
                    # segue direto (sem pegar vaga de novo: o semáforo é do host atual)
                    novo = urllib.parse.urljoin(url, destino)
                    p = urllib.parse.urlsplit(novo)
                    caminho_novo = p.path + (("?" + p.query) if p.query else "")
                    return _ler_com_conexao(novo, p.scheme, p.netloc, caminho_novo, ini, fim, tentativa)
                raise IOError(f"redirecionamento sem destino ({r.status})")
            if r.status not in (200, 206):
                raise IOError(f"HTTP {r.status}")
            faixa = r.getheader("Content-Range") or ""
            fim_txt = faixa.split("/")[-1] if "/" in faixa else ""
            total = int(fim_txt) if fim_txt.isdigit() else 0
            return corpo, total
        except Exception:
            descartar(esquema, host)          # conexão pode ter ficado ruim: começa outra
            if resto == TENTATIVAS - tentativa - 1:
                raise
    raise IOError("sem resposta")


def caixas(dados):
    """(tipo, inicio do conteúdo, tamanho) de cada caixa irmã em `dados`."""
    i = 0
    while i + 8 <= len(dados):
        tam = struct.unpack(">I", dados[i:i + 4])[0]
        tipo = dados[i + 4:i + 8].decode("latin1")
        cab = 8
        if tam == 1:
            if i + 16 > len(dados):
                return
            tam = struct.unpack(">Q", dados[i + 8:i + 16])[0]
            cab = 16
        elif tam == 0:
            tam = len(dados) - i
        if tam < cab:
            return
        yield tipo, i + cab, tam
        i += tam


def cabecalho(url, off):
    """(tipo, offset, cabeçalho, tamanho, bytes, total) da caixa que começa em `off`."""
    dados, total = ler(url, off, off + JANELA - 1)
    if len(dados) < 8:
        return None
    tam = struct.unpack(">I", dados[:4])[0]
    tipo = dados[4:8].decode("latin1")
    cab = 8
    if tam == 1:
        if len(dados) < 16:
            return None
        tam = struct.unpack(">Q", dados[8:16])[0]
        cab = 16
    elif tam == 0:
        tam = max(0, total - off)
    if tam < cab:
        return None
    return tipo, off, cab, tam, dados, total


def idioma_mdhd(dados, ini):
    """Idioma ISO-639-2 empacotado em 15 bits (3 letras de 5 bits + 0x60).

    mdhd: versão/flags (4) + criação/modificação/timescale/duração + language (2).
    """
    if ini + 22 > len(dados):
        return None
    versao = dados[ini]
    off = ini + (32 if versao == 1 else 20)
    if off + 2 > len(dados):
        return None
    bits = struct.unpack(">H", dados[off:off + 2])[0] & 0x7FFF
    return "".join(chr(((bits >> s) & 0x1F) + 0x60) for s in (10, 5, 0))


def info_da_faixa(dados, cab):
    """(handler, idioma) de uma caixa trak cujo conteúdo começa em `cab`."""
    handler = lang = None
    for tipo, cont, tam in caixas(dados[cab:]):
        if tipo != "mdia":
            continue
        for t2, c2, _ in caixas(dados[cab + cont:cab + cont + tam]):
            if t2 == "mdhd":
                lang = idioma_mdhd(dados, cab + cont + c2)
            elif t2 == "hdlr" and cab + cont + c2 + 12 <= len(dados):
                handler = dados[cab + cont + c2 + 8:cab + cont + c2 + 12].decode("latin1")
        break
    return handler, lang


def faixas(url):
    """[(handler, idioma)] de todas as faixas do arquivo."""
    # Um único pedaço inicial costuma trazer ftyp, moov, mvhd e a 1ª faixa.
    dados, total = ler(url, 0, INICIAL - 1)
    moov = None
    for tipo, cont, tam in caixas(dados):
        if tipo == "moov":
            moov = (cont - 8, tam)
            break
    if moov is None:                          # arquivo sem faststart: moov depois do mdat
        off = 0
        for _ in range(6):
            h = cabecalho(url, off)
            if not h:
                return None, "arquivo ilegível"
            tipo, o, cab, tam, _, total = h
            if tipo == "moov":
                moov = (o, tam)
                break
            off += tam
    if not moov:
        return None, "moov não encontrado"

    inicio_moov, tam_moov = moov
    saida: list = []
    pos = inicio_moov + 8
    for _ in range(MAX_FAIXAS * 3):
        if pos >= inicio_moov + tam_moov or (total and pos >= total):
            break
        # usa o pedaço que já está na memória quando ele cobre esta caixa
        if pos + 8 <= len(dados):
            bloco, base = dados, 0
        else:
            bloco, _ = ler(url, pos, pos + INICIAL - 1)
            base = pos
            if len(bloco) < 8:
                break
        rel = pos - base
        tam = struct.unpack(">I", bloco[rel:rel + 4])[0]
        tipo = bloco[rel + 4:rel + 8].decode("latin1")
        cab = 8
        if tam == 1:
            if rel + 16 > len(bloco):
                break
            tam = struct.unpack(">Q", bloco[rel + 8:rel + 16])[0]
            cab = 16
        elif tam == 0:
            tam = max(0, total - pos)
        if tam < cab:
            break
        if tipo == "trak":
            handler, lang = info_da_faixa(bloco, rel + cab)
            if handler is None:              # tabelas grandes logo após o tkhd
                mais, _ = ler(url, pos, pos + 4 * JANELA - 1)
                handler, lang = info_da_faixa(mais, cab)
            saida.append((handler, lang))
        pos += tam
    return saida, ""


def classificar(url):
    """pt (tem faixa em português) | original (faixas identificadas, nenhuma em pt) | indefinido."""
    for tentativa in range(TENTATIVAS):
        try:
            lista, erro = faixas(url)
            if lista is None:
                return {"audios": [], "status": "erro", "detalhe": erro}
            audio = [lang for handler, lang in lista if handler == "soun"]
            if not audio:
                return {"audios": [], "status": "erro", "detalhe": "sem faixa de áudio lida"}
            if "por" in audio:
                return {"audios": audio, "status": "pt"}
            return {"audios": audio, "status": "original" if any(a != "und" for a in audio) else "indefinido"}
        except Exception as e:               # rede, 403, host fora do ar...
            if tentativa == TENTATIVAS - 1:
                return {"audios": [], "status": "erro", "detalhe": str(e)[:120]}
    return {"audios": [], "status": "erro", "detalhe": "sem resposta"}


def titulos():
    """Um item por título: séries usam o primeiro episódio; filmes, o próprio filme."""
    saida = []
    for nome in sorted(os.listdir(PASTA)):
        if not nome.startswith("eps-") or not nome.endswith(".json"):
            continue
        with open(os.path.join(PASTA, nome), encoding="utf-8") as f:
            shard = json.load(f)
        for item in shard.get("s", {}).values():
            eps = item.get("e") or []
            if eps and eps[0][3]:
                saida.append({"tipo": "serie", "t": item["t"], "link": eps[0][2], "url": eps[0][3]})
        for item in shard.get("f", {}).values():
            if item.get("v"):
                saida.append({"tipo": "filme", "t": item["t"], "link": item.get("l", ""), "url": item["v"]})
    saida.sort(key=lambda x: (x["tipo"], x["t"].lower()))
    return saida


def main():
    global POR_HOST, TIMEOUT
    ap = argparse.ArgumentParser()
    ap.add_argument("--limite", type=int, default=0)
    ap.add_argument("--paralelo", type=int, default=PARALELO)
    ap.add_argument("--por-host", type=int, default=POR_HOST)
    ap.add_argument("--timeout", type=int, default=TIMEOUT)
    args = ap.parse_args()
    POR_HOST = args.por_host
    TIMEOUT = args.timeout

    lista = titulos()
    feitos = {}
    if os.path.exists(SAIDA):
        try:
            with open(SAIDA, encoding="utf-8") as f:
                feitos = {x["link"]: x for x in json.load(f).get("titulos", [])}
        except (ValueError, KeyError):
            feitos = {}

    pendentes = [x for x in lista if feitos.get(x["link"], {}).get("status") in (None, "erro")]
    if args.limite:
        pendentes = pendentes[:args.limite]
    print(f"{len(lista)} títulos; {len(lista) - len(pendentes)} prontos; {len(pendentes)} nesta rodada", flush=True)

    def tarefa(item):
        return dict(item, **classificar(item["url"]))

    def gravar():
        resultado = sorted(feitos.values(), key=lambda x: (x["tipo"], x["t"].lower()))
        resumo = {"total": len(resultado)}
        for status in ("pt", "original", "indefinido", "erro"):
            resumo[status] = sum(1 for x in resultado if x["status"] == status)
        os.makedirs(os.path.dirname(SAIDA), exist_ok=True)
        with open(SAIDA, "w", encoding="utf-8") as f:
            json.dump({"gerado": datetime.date.today().isoformat(), "resumo": resumo, "titulos": resultado},
                      f, ensure_ascii=False, separators=(",", ":"))
        return resumo

    with ThreadPoolExecutor(max_workers=args.paralelo) as pool:
        for n, res in enumerate(pool.map(tarefa, pendentes), 1):
            feitos[res["link"]] = res
            if n % 200 == 0:
                print(f"  {n}/{len(pendentes)}", flush=True)
            if n % 200 == 0:
                gravar()             # salva no meio do caminho: se a rodada cair, nada se perde

    resumo = gravar()
    resultado = sorted(feitos.values(), key=lambda x: (x["tipo"], x["t"].lower()))

    print(f"\n{SAIDA}: {resumo}")
    for status in ("original", "indefinido"):
        exemplos = [x["t"] for x in resultado if x["status"] == status][:8]
        print(f"  {status}: " + "; ".join(exemplos))
    if resumo["erro"]:
        print(f"  {resumo['erro']} sem resposta — rode de novo para tentar só esses")


if __name__ == "__main__":
    sys.exit(main())
