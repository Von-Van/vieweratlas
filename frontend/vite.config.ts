import { defineConfig, loadEnv, type Plugin } from 'vite'
import path from 'path'
import tailwindcss from '@tailwindcss/vite'
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
    // The React and Tailwind plugins are both required for Make, even if
    // Tailwind is not being actively used – do not remove them
    react(),
    tailwindcss(),
    siteUrlMeta(loadEnv(mode, process.cwd(), 'VITE_').VITE_SITE_URL?.trim() ?? ''),
  ],
  resolve: {
    alias: {
      // Alias @ to the src directory
      '@': path.resolve(__dirname, './src'),
    },
  },

  // File types to support raw imports. Never add .css, .tsx, or .ts files to this.
  assetsInclude: ['**/*.svg', '**/*.csv'],
}))
