import path from 'node:path';
import { defineConfig } from 'vitest/config';

// The `@wh/*` alias mirrors `paths` in tsconfig.json; vite does not read tsconfig paths itself.
export default defineConfig({
  resolve: { alias: { '@wh': path.resolve(import.meta.dirname, 'src') } },
  test: { environment: 'jsdom', globals: true, include: ['tests/**/*.test.{ts,tsx}'] },
});
