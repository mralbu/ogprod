/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** GoatCounter `/count` endpoint; analytics stay off when unset. */
  readonly VITE_GOATCOUNTER_URL?: string;
}
