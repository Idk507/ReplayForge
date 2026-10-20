import js from "@eslint/js";
import ts from "typescript-eslint";
import hooks from "eslint-plugin-react-hooks";
import a11y from "eslint-plugin-jsx-a11y";
export default [
  {ignores: [".next/**", "node_modules-incomplete/**", "test-results/**", "playwright-report/**", "next-env.d.ts"]},
  js.configs.recommended,
  ...ts.configs.recommended,
  {files: ["**/*.tsx", "**/*.ts"], plugins: {"react-hooks": hooks, "jsx-a11y": a11y},
    rules: {...hooks.configs.recommended.rules, ...a11y.configs.recommended.rules},
  },
  {files: ["**/*.mjs"], languageOptions: {globals: {process: "readonly"}}},
];
