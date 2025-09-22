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

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ThemeProvider defaultTheme="system" storageKey="vite-ui-theme">
      <App />
    </ThemeProvider>
  </StrictMode>,
)
