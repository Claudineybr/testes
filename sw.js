/* Clau Flix — Service Worker v11
   Novidades desta versão:
   1) A classificação agora é dividida em shards (classificacao.json é o manifesto +
      classificacao_1.json, classificacao_2.json, ...). O service worker trata
      TODOS esses arquivos como "dados" (rede primeiro, cache só como reserva offline),
      igual ao catálogo em /catalogo/... — senão os shards ficavam presos no cache
      e o usuário não via os gêneros novos.
   2) Mantém todas as correções das versões anteriores:
      - "/index.html" -> "/" (Cloudflare Pages responde 308, que não pode ser
        devolvido numa navegação; o worker pede direto o destino).
      - Respostas com redirecionamento são "limpas" antes de cache/entrega.
      - Cache com nome novo: apaga automaticamente os caches antigos.
      - Catálogo dividido em partes (catalogo/indice.json + catalogo/parte-NNN.json). */

const VERSAO = 'clauflix-v11'; // v11: shards da classificação como "dados" (rede primeiro)

const CACHE_ESTATICOS = `${VERSAO}-estaticos`;
const CACHE_DADOS = `${VERSAO}-dados`;

const BASE = ['/', '/serie', '/player.js'];
const DADOS = ['/classificacao.json'];

/* dados que usam "rede primeiro": classificação (manifesto + shards) e catálogo em partes */
const ehDados = (caminho) =>
  DADOS.includes(caminho) ||
  /^\/classificacao_\d+\.json$/i.test(caminho) ||
  caminho.startsWith('/catalogo/');

/* "/index.html" -> "/", "/serie.html" -> "/serie" (URL final do Cloudflare Pages) */
function canonica(href) {
  const u = new URL(href);
  u.hash = '';
  if (u.pathname.endsWith('.html')) {
    const semExt = u.pathname.slice(0, -5);
    u.pathname = (semExt === '' || semExt === '/index') ? '/' : semExt;
  }
  return u.toString();
}

/* Uma resposta obtida seguindo redirecionamento não pode ser devolvida
   numa navegação nem guardada no cache. Recriar a resposta resolve. */
async function semRedirecionamento(resp) {
  if (!resp || !resp.redirected) return resp;
  const corpo = await resp.arrayBuffer();
  return new Response(corpo, {
    status: resp.status,
    statusText: resp.statusText,
    headers: resp.headers,
  });
}

self.addEventListener('install', (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(CACHE_ESTATICOS);
      await Promise.all(BASE.map((url) => cache.add(url).catch(() => {})));
      await self.skipWaiting();
    })()
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      const nomes = await caches.keys();
      await Promise.all(
        nomes.filter((n) => !n.startsWith(VERSAO)).map((n) => caches.delete(n))
      );
      await self.clients.claim();
    })()
  );
});

/* catálogo, manifesto e shards da classificação: rede primeiro, cache só como reserva offline */
async function redePrimeiro(req, chave) {
  const cache = await caches.open(CACHE_DADOS);
  try {
    const resp = await semRedirecionamento(await fetch(req.url));
    if (resp && resp.ok) cache.put(chave, resp.clone());
    return resp;
  } catch (erro) {
    const salvo = await cache.match(chave);
    if (salvo) return salvo;
    throw erro;
  }
}

/* páginas e estáticos: cache primeiro, atualizando em segundo plano */
async function cachePrimeiro(event, req) {
  const alvo = canonica(req.url);
  const chave = new URL(alvo).pathname;
  const cache = await caches.open(CACHE_ESTATICOS);
  const salvo = await cache.match(chave);

  const rede = (async () => {
    try {
      const resp = await semRedirecionamento(await fetch(alvo));
      if (resp && resp.ok) await cache.put(chave, resp.clone());
      return resp;
    } catch (erro) {
      return null;
    }
  })();

  if (salvo) {
    event.waitUntil(rede);
    return salvo;
  }

  const resp = await rede;
  if (resp) return resp;

  if (req.mode === 'navigate') {
    const home = await cache.match('/');
    if (home) return home;
  }
  return new Response('Offline', { status: 503, statusText: 'Offline' });
}

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  if (ehDados(url.pathname)) {
    event.respondWith(redePrimeiro(req, url.pathname));
    return;
  }

  event.respondWith(cachePrimeiro(event, req));
});
