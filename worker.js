/**
 * Cloudflare Worker — CORS proxy for Anthropic API
 *
 * Deploy steps:
 *  1. Go to https://dash.cloudflare.com → Workers & Pages → Create → Worker
 *  2. Paste this entire file into the editor and click Save & Deploy
 *  3. Copy the worker URL (e.g. https://anthropic-proxy.yourname.workers.dev)
 *  4. Paste it into the "Proxy URL" field in AI Card Studio Settings
 */

const ANTHROPIC_BASE = "https://api.anthropic.com";

export default {
  async fetch(request) {
    const origin = request.headers.get("Origin") || "*";

    // Handle CORS preflight
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: corsHeaders(origin) });
    }

    // Build target URL
    const url = new URL(request.url);
    const target = ANTHROPIC_BASE + url.pathname + url.search;

    // Forward headers, strip hop-by-hop
    const headers = new Headers(request.headers);
    for (const h of ["host", "origin", "referer", "cf-connecting-ip", "cf-ray", "cf-visitor"]) {
      headers.delete(h);
    }

    // Proxy the request, stream the response body
    const upstream = await fetch(target, {
      method: request.method,
      headers,
      body: ["GET", "HEAD"].includes(request.method) ? undefined : request.body,
    });

    const respHeaders = new Headers(upstream.headers);
    respHeaders.set("Access-Control-Allow-Origin", origin);
    respHeaders.set("Access-Control-Allow-Credentials", "true");

    return new Response(upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: respHeaders,
    });
  },
};

function corsHeaders(origin) {
  return {
    "Access-Control-Allow-Origin": origin,
    "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, PATCH, OPTIONS",
    "Access-Control-Allow-Headers": "*",
    "Access-Control-Max-Age": "86400",
  };
}
