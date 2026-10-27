import { defineConfig, mergeConfig } from 'vite'
import base from './vite.config'

// Untracked, for the screenshots of REB-739 only: the dev proxy on this agent's own API port.
export default mergeConfig(
  base,
  defineConfig({
    server: { port: 5140, strictPort: true, proxy: { '/api': { target: 'http://localhost:8140', changeOrigin: true } } },
  }),
)
