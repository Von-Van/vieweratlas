import { defineConfig, loadEnv, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'

/**
 * Inject the canonical and og:url tags, but only once a real site URL exists.
 *
 * Both tags need an absolute origin, which is not known until a custom domain
 * is registered. Emitting them with a placeholder is worse than omitting them —
 * a wrong canonical actively misdirects crawlers — so this stays silent while
 * VITE_SITE_URL is unset.
 */
function siteUrlMeta(siteUrl: string): Plugin {
  return {
    name: 'vieweratlas-site-url-meta',
    transformIndexHtml() {
      if (!siteUrl) return []
      const origin = siteUrl.replace(/\/+$/, '')
      return [
        { tag: 'link', attrs: { rel: 'canonical', href: origin }, injectTo: 'head' },
        { tag: 'meta', attrs: { property: 'og:url', content: origin }, injectTo: 'head' },
      ]
    },
  }
}

export default defineConfig(({ mode }) => ({
  plugins: [
    react(),
    siteUrlMeta(loadEnv(mode, process.cwd(), 'VITE_').VITE_SITE_URL?.trim() ?? ''),
  ],
}))
