#!/usr/bin/env python3
"""Sobe para https:// os links de vídeo dos hosts que comprovadamente têm TLS.

Por que isso importa:
  * página em https com vídeo em http:// vai para o proxy do Worker, que ignora
    Range (não dá para pular abertura nem buscar posição) e abre mais devagar;
  * o Rave lê o contentUrl da página: em http:// ele leva um 301 para https antes
    de começar, e em Android http puro costuma ser bloqueado/repetido.

Regra: SÓ troca quando o host está em HOSTS_COM_TLS. Host novo ou desconhecido
continua como veio — forçar https num host sem TLS quebra o vídeo de vez.

Idempotente: rodar de novo não altera nada.
"""

import base64
import glob
import re
import sys
from pathlib import Path

# Hosts verificados com Range 206 em https (medido em 30 URLs por host).
# Ao descobrir um host novo, teste antes com:
#   curl -o /dev/null -w '%{http_code}\n' -r 0-1023 https://HOST/caminho.mp4
HOSTS_COM_TLS = {
    "fhd1.oneplayer.site",
    "fhd4.oneplayer.site",
    "hd5.oneplayer.site",
    "shd0.oneplayer.site",
    "shd1.oneplayer.site",
    "shd2.oneplayer.site",
    "shd7.oneplayer.site",
    "shd8.oneplayer.site",
    "shd9.oneplayer.site",
    "play-1.cnvods.com",
}

RAIZ = Path(__file__).resolve().parents[2]

# http://host/... — a decisão de trocar ou não é feita pelo allowlist acima.
RX_HTTP = re.compile(r"http://([a-z0-9.-]+)/", re.I)
# Páginas novas guardam o link em base64 (atob("...")): precisa reescrever lá também.
RX_B64 = re.compile(r'atob\("([A-Za-z0-9+/=]+)"\)')


def sobe_para_https(url):
    """Devolve a url em https:// ou None quando o host não está no allowlist."""
    m = re.match(r"^http://([a-z0-9.-]+)/", url, re.I)
    if not m or m.group(1).lower() not in HOSTS_COM_TLS:
        return None
    return "https://" + url[7:]


def corrigir_texto(texto):
    return RX_HTTP.sub(
        lambda m: ("https://" if m.group(1).lower() in HOSTS_COM_TLS else "http://") + m.group(1) + "/",
        texto,
    )


def corrigir_base64(texto):
    def rep(m):
        try:
            url = base64.b64decode(m.group(1)).decode("utf-8")
        except Exception:
            return m.group(0)
        nova = sobe_para_https(url)
        if not nova:
            return m.group(0)
        return 'atob("' + base64.b64encode(nova.encode()).decode() + '")'

    return RX_B64.sub(rep, texto)


def processar(caminho, encoding="utf-8"):
    destino = Path(caminho)
    original = destino.read_text(encoding=encoding)
    novo = corrigir_base64(corrigir_texto(original))
    if novo == original:
        return False
    destino.write_text(novo, encoding=encoding, newline="")
    return True


def main():
    alterados = 0

    paginas = sorted(glob.glob(str(RAIZ / "*.html")))
    for pagina in paginas:
        try:
            alterados += processar(pagina)
        except Exception as erro:
            print(f"falhou em {pagina}: {erro}", file=sys.stderr)

    dados = sorted(glob.glob(str(RAIZ / "catalogo" / "*.json"))) + sorted(glob.glob(str(RAIZ / "classificacao*.json")))
    for arquivo in dados:
        try:
            alterados += processar(arquivo)
        except Exception as erro:
            print(f"falhou em {arquivo}: {erro}", file=sys.stderr)

    print(f"páginas verificadas: {len(paginas)} | arquivos de dados: {len(dados)} | alterados: {alterados}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
