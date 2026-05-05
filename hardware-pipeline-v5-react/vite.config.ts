import path from 'path';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';
import { viteSingleFile } from 'vite-plugin-singlefile';

// Freeze the build time at bundle-compile time, in IST (the user's zone),
// so the build-tag on screen matches their system clock and confirms freshness.
// Format: YYYY-MM-DD HH:mm
const _now = new Date();
const _ist = new Date(_now.getTime() + 5.5 * 60 * 60 * 1000);
const _pad = (n: number) => String(n).padStart(2, '0');
const BUILD_TIME_IST =
  `${_ist.getUTCFullYear()}-${_pad(_ist.getUTCMonth() + 1)}-${_pad(_ist.getUTCDate())} ` +
  `${_pad(_ist.getUTCHours())}:${_pad(_ist.getUTCMinutes())}`;

export default defineConfig({
  plugins: [react(), viteSingleFile()],
  resolve: { alias: { '@': path.resolve(__dirname, './src') } },
  define: {
    __BUILD_TIME_IST__: JSON.stringify(BUILD_TIME_IST + ' IST'),
  },
  build: {
    target: 'es2015',
    // v15 — the sandbox sometimes holds immutable flags on existing dist files;
    // disabling emptyOutDir lets the build overwrite in place.
    emptyOutDir: false,
  },
});
