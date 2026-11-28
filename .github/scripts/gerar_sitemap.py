#!/usr/bin/env python3
"""Gera sitemap.xml (índice) + sitemap-N.xml a partir das páginas do repositório.

Cada título do catálogo é uma página estática (ex.: 473768.html), publicada sem
extensão (/473768). O protocolo de sitemaps limita 50.000 URLs por arquivo, então
as entradas são divididas em partes e listadas por um índice.

Uso: python3 .github/scripts/gerar_sitemap.py
"""
import os
from pathlib import Path

BASE = os.environ.get("SITE_URL", "https://testes-f1t.pages.dev").rstrip("/")
RAIZ = Path(__file__).resolve().parents[2]
POR_ARQUIVO = 45000
IGNORAR = {"index.html", "serie.html", "404.html"}


def paginas():
    yield BASE + "/"
    for caminho in sorted(RAIZ.glob("*.html")):
        if caminho.name in IGNORAR:
            continue
        yield f"{BASE}/{caminho.stem}"


def escrever(qtd_partes, total):
    indice = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for numero in range(1, qtd_partes + 1):
        indice.append(f"  <sitemap><loc>{BASE}/sitemap-{numero}.xml</loc></sitemap>")
    indice.append("</sitemapindex>")
    (RAIZ / "sitemap.xml").write_text("\n".join(indice) + "\n", encoding="utf-8")

    print(f"sitemap.xml: {qtd_partes} parte(s), {total} URLs")


def main():
    lote = []
    partes = 0
    total = 0

    def fechar():
        nonlocal partes, lote
        partes += 1
        corpo = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
        ]
        corpo += [f"  <url><loc>{url}</loc></url>" for url in lote]
        corpo.append("</urlset>")
        (RAIZ / f"sitemap-{partes}.xml").write_text("\n".join(corpo) + "\n", encoding="utf-8")
        lote = []

    for url in paginas():
        lote.append(url)
        total += 1
        if len(lote) >= POR_ARQUIVO:
            fechar()
    if lote:
        fechar()

    # Remove partes de execuções anteriores que sobraram.
    for antigo in RAIZ.glob("sitemap-*.xml"):
        numero = antigo.stem.split("-")[-1]
        if numero.isdigit() and int(numero) > partes:
            antigo.unlink()

    escrever(partes, total)


if __name__ == "__main__":
    main()
