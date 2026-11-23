/* Clau Flix — Service Worker. Páginas: cache primeiro. Catálogo/classificação (/catalogo/*, classificacao*.json): rede primeiro.
   Respostas redirecionadas (308 do Pages em /index.html) são recriadas, senão a navegação falha. Mude VERSAO ao alterar os arquivos. */

const VERSAO = 'clauflix-v14';

const CACHE_ESTATICOS = `${VERSAO}-estaticos`;
const CACHE_DADOS = `${VERSAO}-dados`;

const BASE = ['/', '/serie', '/player.js'];
const DADOS = ['/classificacao.json'];

const ehDados = (caminho) => DADOS.includes(caminho) || caminho.startsWith('/catalogo/') || /^\/classificacao_\d+\.json$/.test(caminho);

function canonica(href) {
  const u = new URL(href);
  u.hash = '';
  if (u.pathname.endsWith('.html')) {
    const semExt = u.pathname.slice(0, -5);
    u.pathname = (semExt === '' || semExt === '/index') ? '/' : semExt;
  }
  return u.toString();
}

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
