// Runs the same tests as vitest. `moduleNameMapper` mirrors `paths` in tsconfig.json and lets a
// `./x.js` import find `x.ts`. ts-jest only transpiles (isolatedModules): type errors are tsc's job.
module.exports = {
  testEnvironment: 'jsdom',
  testMatch: ['<rootDir>/tests/**/*.test.{ts,tsx}'],
  moduleNameMapper: { '^@wh/(.*)$': '<rootDir>/src/$1', '^(\\.{1,2}/.*)\\.js$': '$1' },
  transform: {
    '^.+\\.tsx?$': [
      'ts-jest',
      {
        diagnostics: false,
        tsconfig: {
          isolatedModules: true,
          target: 'ES2022',
          module: 'commonjs',
          moduleResolution: 'node10',
          jsx: 'react-jsx',
          esModuleInterop: true,
          experimentalDecorators: true,
        },
      },
    ],
  },
};
