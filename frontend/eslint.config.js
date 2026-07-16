import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'

export default tseslint.config(
  // animate-ui 為 shadcn registry 匯入的第三方元件（官方 CLI 拉入、依其自身慣例撰寫），不套本專案 lint 規則
  {
    ignores: [
      'dist',
      'node_modules',
      'src/components/animate-ui/**',
      'src/hooks/use-controlled-state.tsx',
      'src/hooks/use-is-in-view.tsx',
      'src/lib/get-strict-context.tsx',
    ],
  },
  {
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    files: ['**/*.{ts,tsx}'],
    languageOptions: { ecmaVersion: 2022, globals: globals.browser },
    plugins: { 'react-hooks': reactHooks, 'react-refresh': reactRefresh },
    rules: {
      ...reactHooks.configs.recommended.rules,
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
    },
  },
)
