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
      // 本 repo 既有慣例是以 `_` 前綴表示「刻意不用」（測試的 `(..._a)`、
      // 解構時 `const { file_hash: _omitted, ...rest }` 這種「拔掉一個鍵」寫法）。
      // 預設設定會把這些判成錯，於是唯一的解法變成改寫成更難讀的寫法——把慣例
      // 寫進規則才是正解。ignoreRestSiblings 專門對應那個 rest 解構 idiom。
      '@typescript-eslint/no-unused-vars': ['error', {
        argsIgnorePattern: '^_',
        varsIgnorePattern: '^_',
        caughtErrorsIgnorePattern: '^_',
        ignoreRestSiblings: true,
      }],
    },
  },
)
