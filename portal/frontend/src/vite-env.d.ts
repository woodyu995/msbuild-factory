/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_PORTAL_API_TOKEN?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
