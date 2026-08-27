// User-level Cordis plugin: web-search-adapter
//
// 注册一个免 Key 的国内直连 Web Search Provider，对接 DuckDuckGo Lite 版，
// 解决本地 DSH 内置 web_search 无 API Key 时不可用的问题。
//
// - provider id: duckduckgo-lite
// - endpoint: https://lite.duckduckgo.com/lite/ (国内可直连，无需 API Key)
// - 返回结构化 sources: [{url, title, snippet}]
//
// 为什么用 DuckDuckGo Lite 而不是 html.duckduckgo.com/html/：
//   2026-08-28 实测：html.duckduckgo.com/html/ 会把本机出口 IP 拦到
//   anomaly 验证码页（HTTP 200 但无结果，解析恒为空）；lite 端点同网络
//   环境下返回 10 条真实结果、无验证码，解析器已对实时响应验证通过。
//
// 部署：把本文件复制到 ~/.dsh/profiles/<profile>/plugins/，
// 并在 cordis.patch.yml 中合并对应 insert 片段。

export const name = 'web-search-adapter';
export const inject = ['web'];

const PROVIDER_ID = 'duckduckgo-lite';
const DEFAULT_ENDPOINT = 'https://lite.duckduckgo.com/lite/';
const DEFAULT_TIMEOUT_MS = 15_000;
const DEFAULT_MAX_RESULTS = 10;

function decodeHtmlEntities(text) {
  return text
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&nbsp;/g, ' ');
}

// DDG Lite 结果链接是跳转壳：//duckduckgo.com/l/?uddg=<urlencoded>&rut=...
// 取 uddg 参数还原真实 URL；已是绝对 http(s) 的直接用。
function decodeDdgUrl(href) {
  const m = href.match(/[?&]uddg=([^&]+)/);
  if (m) return decodeURIComponent(m[1]);
  if (/^https?:\/\//.test(href)) return href;
  return null;
}

// 结构（2026-08-28 实测）：
//   <a rel="nofollow" href="//duckduckgo.com/l/?uddg=..." class='result-link'>标题</a>
//   其后紧随 <td class='result-snippet'>摘要</td>
function parseDdgLite(html, maxResults) {
  const sources = [];
  const anchorRe = /<a\b[^>]*class='result-link'[^>]*>([\s\S]*?)<\/a>/g;
  let match;
  while ((match = anchorRe.exec(html)) !== null && sources.length < maxResults) {
    const tag = match[0];
    const hrefMatch = tag.match(/href="([^"]+)"/);
    if (!hrefMatch) continue;
    const url = decodeDdgUrl(decodeHtmlEntities(hrefMatch[1]));
    if (!url) continue;
    const title = decodeHtmlEntities(match[1].replace(/<[^>]+>/g, '').trim());
    const rest = html.slice(match.index + tag.length, match.index + tag.length + 2000);
    const snipMatch = rest.match(/<td class='result-snippet'>([\s\S]*?)<\/td>/);
    const snippet = snipMatch
      ? decodeHtmlEntities(snipMatch[1].replace(/<[^>]+>/g, '').replace(/\s+/g, ' ').trim())
      : '';
    if (title) sources.push({ url, title, snippet });
  }
  return { sources, truncated: false };
}

class DuckDuckGoLiteProvider {
  constructor(config = {}) {
    this.id = PROVIDER_ID;
    this.endpoint = config.endpoint || DEFAULT_ENDPOINT;
    this.timeoutMs = config.timeoutMs || DEFAULT_TIMEOUT_MS;
    this.maxResults = config.maxResults || DEFAULT_MAX_RESULTS;
  }

  available() {
    return true; // 无需 Key，始终可用
  }

  async search(request, signal) {
    const query = request?.query || '';
    if (!query.trim()) {
      return { sources: [], truncated: false };
    }
    const url = `${this.endpoint}?q=${encodeURIComponent(query)}`;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), this.timeoutMs);
    const onAbort = () => controller.abort();
    if (signal) signal.addEventListener('abort', onAbort, { once: true });
    try {
      const response = await fetch(url, {
        method: 'GET',
        headers: {
          'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
          'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
          'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8'
        },
        signal: controller.signal
      });
      clearTimeout(timeout);
      if (!response.ok) {
        throw new Error(`DuckDuckGo Lite search failed: HTTP ${response.status}`);
      }
      const html = await response.text();
      return parseDdgLite(html, this.maxResults);
    } catch (error) {
      clearTimeout(timeout);
      if (error.name === 'AbortError') {
        throw error;
      }
      throw new Error(`DuckDuckGo Lite search error: ${error.message}`);
    } finally {
      if (signal) signal.removeEventListener('abort', onAbort);
    }
  }
}

export function apply(ctx, config) {
  const provider = new DuckDuckGoLiteProvider(config);
  ctx.web.registerSearchProvider(provider);
}
