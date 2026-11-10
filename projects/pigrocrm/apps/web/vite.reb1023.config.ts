import { mergeConfig } from 'vite'
import base from './vite.config'

// Untracked, deleted before the final push: points the dev proxy at my own API for rendering REB-1023.
export default mergeConfig(base, {
  server: { proxy: { '/api': { target: 'http://localhost:8731', changeOrigin: true } } },
})
