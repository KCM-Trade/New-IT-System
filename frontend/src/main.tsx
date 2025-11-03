import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
// Global ag-Grid styles to ensure theme CSS loads after Tailwind
import 'ag-grid-community/styles/ag-grid.css'
import 'ag-grid-community/styles/ag-theme-quartz.css'
import { ModuleRegistry, AllCommunityModule } from 'ag-grid-community'
// Register all Community features once globally (fixes AG Grid error #272)
ModuleRegistry.registerModules([AllCommunityModule])
import App from './App'
import { ThemeProvider } from '@/components/theme-provider'
import { I18nProvider } from '@/components/i18n-provider'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <I18nProvider defaultLang="zh-CN" storageKey="vite-ui-language">
      <ThemeProvider defaultTheme="system" storageKey="vite-ui-theme">
        <App />
      </ThemeProvider>
    </I18nProvider>
  </StrictMode>,
)
