import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';
import '@fontsource-variable/inter';
import './lib/embedded';
import './lib/smartShuffle';
import { startUpdateWatcher } from './lib/update';
import './index.css';

// Every entry gets an index so the in-app "back" button knows when to stop (see lib/router).
if (!window.history.state || typeof (window.history.state as { idx?: unknown }).idx !== 'number') {
  window.history.replaceState({ idx: 0 }, '');
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

startUpdateWatcher();

if ('serviceWorker' in navigator && (import.meta.env.PROD || import.meta.env.VITE_SW === '1')) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch((e) => console.warn('service worker registration failed', e));
  });
}
