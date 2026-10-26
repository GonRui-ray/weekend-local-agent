import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig(({mode})=>{
  const env=loadEnv(mode,'.','');
  return {base:env.VITE_PUBLIC_BASE||'/ui-app/',plugins:[react()],server:{port:5173,proxy:{'/turn':'http://127.0.0.1:7860','/api':'http://127.0.0.1:7860'}}};
});
