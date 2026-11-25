/* Clau Flix — Service Worker.
   Navegação (páginas): rede primeiro, cache como reserva offline — assim uma atualização do site aparece na hora.
   Catálogo/classificação (/catalogo/*, classificacao*.json): rede primeiro.
   Demais arquivos do mesmo domínio: cache primeiro com revalidação em segundo plano.
   Respostas redirecionadas (308 do Pages em /index.html) são recriadas, senão a navegação falha. Mude VERSAO ao alterar os arquivos. */

const VERSAO = 'clauflix-v21';

const CACHE_ESTATICOS = `${VERSAO}-estaticos`;
const CACHE_DADOS = `${VERSAO}-dados`;

const BASE = ['/', '/serie', '/player.js', '/manifest.webmanifest'];
const DADOS = ['/classificacao.json'];

const ehDados = (caminho) => DADOS.includes(caminho) || caminho.startsWith('/catalogo/') || /^\/classificacao_\d+\.json$/.test(caminho);

// O Pages devolve a home (200 text/html) para arquivo inexistente: isso nunca deve entrar no cache.
const ehHtml = (resp) => ((resp.headers.get('content-type') || '').toLowerCase().includes('text/html'));

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
    if (resp && resp.ok && !ehHtml(resp)) cache.put(chave, resp.clone());
    return resp;
  } catch (erro) {
    const salvo = await cache.match(chave);
    if (salvo) return salvo;
    throw erro;
  }
}

async function redePrimeiroPagina(req) {
  const chave = new URL(canonica(req.url)).pathname;
  const cache = await caches.open(CACHE_ESTATICOS);
  try {
    const resp = await semRedirecionamento(await fetch(req.url));
    if (resp && resp.ok) await cache.put(chave, resp.clone());
    return resp;
  } catch (erro) {
    const salvo = await cache.match(chave);
    if (salvo) return salvo;
    const home = (await cache.match('/')) || (await cache.match('/index.html'));
    if (home) return home;
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
      if (resp && resp.ok && !ehHtml(resp)) await cache.put(chave, resp.clone());
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

  if (req.mode === 'navigate') {
    event.respondWith(redePrimeiroPagina(req).catch(() => new Response('Offline', { status: 503, statusText: 'Offline' })));
    return;
  }

  if (ehDados(url.pathname)) {
    event.respondWith(redePrimeiro(req, url.pathname));
    return;
  }

  event.respondWith(cachePrimeiro(event, req));
});
