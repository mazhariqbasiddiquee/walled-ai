/**
 * Preload: exposes a small, typed API as `window.guard`.
 * The renderer never sees the token or raw IPC.
 */
import { contextBridge, ipcRenderer } from 'electron'
import type { GuardApi, GuardState } from '../shared/types'

function subscribe<T>(channel: string, cb: (payload: T) => void): () => void {
  const listener = (_e: Electron.IpcRendererEvent, payload: T) => cb(payload)
  ipcRenderer.on(channel, listener)
  return () => ipcRenderer.removeListener(channel, listener)
}

const api: GuardApi = {
  getState: () => ipcRenderer.invoke('guard:getState'),
  onState: (cb) => subscribe<GuardState>('guard:state', cb),
  onEvent: (cb) => subscribe<string>('guard:event', cb),
  decide: (input) => ipcRenderer.invoke('guard:decide', input),
  listRequests: (filter) => ipcRenderer.invoke('guard:listRequests', filter),
  stats: () => ipcRenderer.invoke('guard:stats'),
  listRules: () => ipcRenderer.invoke('guard:listRules'),
  addRule: (rule) => ipcRenderer.invoke('guard:addRule', rule),
  deleteRule: (id) => ipcRenderer.invoke('guard:deleteRule', id),
  getSettings: () => ipcRenderer.invoke('guard:getSettings'),
  updateSettings: (patch) => ipcRenderer.invoke('guard:updateSettings', patch),
  openMainWindow: () => ipcRenderer.send('guard:openMainWindow')
}

contextBridge.exposeInMainWorld('guard', api)
