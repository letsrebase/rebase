import { mergeConfig } from 'vite'
import base from './vite.config'

export default mergeConfig(base as never, {
  server: { port: 5110, strictPort: true, proxy: { '/api': { target: 'http://localhost:8110', changeOrigin: true } } },
})
