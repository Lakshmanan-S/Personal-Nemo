import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'


// https://vite.dev/config/
export default defineConfig(({ command, mode }) => {
  const env = loadEnv(mode, process.cwd(), 'VITE_')
  if (command === 'build' && env.VITE_PUBLIC_DEMO === 'true') {
    let url: URL
    try {
      url = new URL(env.VITE_API_BASE_URL)
    } catch {
      throw new Error('Public demo builds require VITE_API_BASE_URL with the HTTPS Render backend origin.')
    }
    if (url.protocol !== 'https:' || url.pathname !== '/' || url.search || url.hash || url.username || url.password) {
      throw new Error('VITE_API_BASE_URL must be an HTTPS origin without a path, query or credentials.')
    }
  }
  return {
    plugins: [react(), tailwindcss()],
    server: { port: 5173 },
  }
})




