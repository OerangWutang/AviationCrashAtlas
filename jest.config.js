/** @type {import('jest').Config} */
export default {
  preset: "ts-jest/presets/default-esm",
  testEnvironment: "node",
  extensionsToTreatAsEsm: [".ts"],
  moduleFileExtensions: ["ts", "tsx", "js", "jsx", "json"],
  transform: {
    "^.+\\.tsx?$": [
      "ts-jest",
      {
        useESM: true,
        diagnostics: { ignoreCodes: [1343] },
        tsconfig: {
          target: "ES2022",
          module: "ESNext",        // ESNext works better with ts-jest ESM
          moduleResolution: "NodeNext",
          strict: true,
          esModuleInterop: true,
          skipLibCheck: true,
          rootDir: ".",
        },
      },
    ],
  },
  // Strip .js extensions so Jest resolves .ts files
  moduleNameMapper: {
    "^(\\.{1,2}/.*)\\.js$": "$1",
  },
  testMatch: ["**/tests/**/*.test.ts"],
  forceExit: true,
  testTimeout: 20000,
};
