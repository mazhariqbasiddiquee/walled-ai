/**
 * Electron main process.
 *
 * - Main window: pending queue, history, rules, settings.
 * - Approval popup: a small always-on-top window that appears whenever a tool
 *   call is waiting for a decision.
 * - Tray / menu-bar item with the pending count; closing windows keeps the app
 *   running so approvals can still be prompted.
 */
import { app, BrowserWindow, ipcMain, Menu, nativeImage, Notification, shell, Tray } from 'electron'
import { existsSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import type { DecisionInput, GuardState, ListFilter, RuleInput, ToolRequest } from '../shared/types'
import { GuardClient } from './guard'

const guard = new GuardClient()
let mainWindow: BrowserWindow | null = null
let popupWindow: BrowserWindow | null = null
let tray: Tray | null = null
let quitting = false

const secureWebPreferences = {
  preload: join(__dirname, '../preload/index.js'),
  contextIsolation: true,
  nodeIntegration: false,
  sandbox: true
}

function loadRenderer(win: BrowserWindow, view: 'main' | 'popup'): void {
  const devUrl = process.env.ELECTRON_RENDERER_URL
  if (devUrl) void win.loadURL(`${devUrl}#${view}`)
  else void win.loadFile(join(__dirname, '../renderer/index.html'), { hash: view })
}

function lockDown(win: BrowserWindow): void {
  // Never navigate away from the app or open arbitrary windows.
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith('http://127.0.0.1') || url.startsWith('https://')) void shell.openExternal(url)
    return { action: 'deny' }
  })
  win.webContents.on('will-navigate', (e) => e.preventDefault())
  // If a renderer dies, reload it: a dead popup would mean silently missed approvals.
  win.webContents.on('render-process-gone', (_e, details) => {
    console.error(`[mcp-guard] renderer gone (${details.reason}, exit ${details.exitCode}); reloading`)
    if (!win.isDestroyed()) setTimeout(() => !win.isDestroyed() && win.webContents.reload(), 500)
  })
}

function createMainWindow(): BrowserWindow {
  const win = new BrowserWindow({
    width: 1100,
    height: 720,
    minWidth: 820,
    minHeight: 520,
    title: 'MCP Guard',
    titleBarStyle: process.platform === 'darwin' ? 'hiddenInset' : 'default',
    backgroundColor: '#0b0d12',
    show: false,
    webPreferences: secureWebPreferences
  })
  lockDown(win)
  win.once('ready-to-show', () => win.show())
  win.on('close', (e) => {
    if (!quitting) {
      e.preventDefault()
      win.hide()
    }
  })
  loadRenderer(win, 'main')
  return win
}

function showMainWindow(): void {
  if (!mainWindow || mainWindow.isDestroyed()) mainWindow = createMainWindow()
  mainWindow.show()
  mainWindow.focus()
}

function createPopupWindow(): BrowserWindow {
  const win = new BrowserWindow({
    width: 480,
    height: 640,
    resizable: false,
    minimizable: false,
    maximizable: false,
    fullscreenable: false,
    alwaysOnTop: true,
    title: 'MCP Guard — Approval required',
    titleBarStyle: process.platform === 'darwin' ? 'hiddenInset' : 'default',
    backgroundColor: '#0b0d12',
    show: false,
    webPreferences: secureWebPreferences
  })
  win.setAlwaysOnTop(true, 'floating')
  win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true })
  lockDown(win)
  win.on('close', (e) => {
    // Closing the popup only hides it; the request stays pending (and will expire).
    if (!quitting) {
      e.preventDefault()
      win.hide()
    }
  })
  loadRenderer(win, 'popup')
  return win
}

function showPopup(): void {
  if (!popupWindow || popupWindow.isDestroyed()) {
    popupWindow = createPopupWindow()
    popupWindow.once('ready-to-show', () => {
      popupWindow?.show()
      popupWindow?.focus()
    })
    return
  }
  popupWindow.show()
  popupWindow.focus()
  if (process.platform === 'darwin') app.focus({ steal: true })
}

function broadcast(channel: string, payload: unknown): void {
  for (const win of BrowserWindow.getAllWindows()) {
    const wc = win.webContents
    if (win.isDestroyed() || wc.isDestroyed() || wc.isCrashed() || wc.isLoading()) continue
    wc.send(channel, payload)
  }
}

function updateTray(state: GuardState): void {
  if (!tray) return
  const n = state.pending.length
  tray.setTitle(state.connected ? (n ? ` ${n} pending` : '') : ' offline')
  tray.setToolTip(state.connected ? `MCP Guard — ${n} pending` : 'MCP Guard — daemon offline')
  tray.setContextMenu(
    Menu.buildFromTemplate([
      { label: state.connected ? `Connected · ${n} pending` : 'Daemon offline', enabled: false },
      { type: 'separator' },
      { label: 'Review pending request…', enabled: n > 0, click: showPopup },
      { label: 'Open MCP Guard', click: showMainWindow },
      { type: 'separator' },
      {
        label: 'Open at login',
        type: 'checkbox',
        enabled: app.isPackaged, // in dev this would register the bare Electron binary
        checked: app.getLoginItemSettings().openAtLogin,
        click: (item) => {
          app.setLoginItemSettings({ openAtLogin: item.checked })
          updateTray(guard.state)
        }
      },
      { type: 'separator' },
      { label: 'Quit', click: () => app.quit() }
    ])
  )
  if (process.platform === 'darwin') app.dock?.setBadge(n ? String(n) : '')
}

function createTray(): void {
  // Text-only menu-bar item on macOS; a tiny shield glyph keeps it visible.
  tray = new Tray(nativeImage.createEmpty())
  tray.setTitle('🛡')
  updateTray(guard.state)
}

function notify(req: ToolRequest): void {
  if (!Notification.isSupported()) return
  const n = new Notification({
    title: 'Approval required',
    body: `${req.server} wants to run ${req.tool}`,
    silent: false
  })
  n.on('click', showPopup)
  n.show()
}

function registerIpc(): void {
  ipcMain.handle('guard:getState', () => guard.state)
  ipcMain.handle('guard:decide', (_e, input: DecisionInput) =>
    guard.request<ToolRequest>('POST', `/api/requests/${encodeURIComponent(input.requestId)}/decision`, {
      decision: input.decision,
      remember: input.remember
    })
  )
  ipcMain.handle('guard:listRequests', (_e, filter: ListFilter) => {
    const q = new URLSearchParams()
    if (filter.status) q.set('status', filter.status)
    if (filter.server) q.set('server', filter.server)
    q.set('limit', String(filter.limit ?? 200))
    return guard.request('GET', `/api/requests?${q}`)
  })
  ipcMain.handle('guard:stats', () => guard.request('GET', '/api/stats'))
  ipcMain.handle('guard:listRules', () => guard.request('GET', '/api/rules'))
  ipcMain.handle('guard:addRule', (_e, rule: RuleInput) => guard.request('POST', '/api/rules', rule))
  ipcMain.handle('guard:deleteRule', (_e, id: number) => guard.request('DELETE', `/api/rules/${Number(id)}`))
  ipcMain.handle('guard:getSettings', () => guard.request('GET', '/api/settings'))
  ipcMain.handle('guard:updateSettings', (_e, patch) => guard.request('PATCH', '/api/settings', patch))
  ipcMain.on('guard:openMainWindow', showMainWindow)
}

/** First launch of the installed app: open at login by default (toggle in the menu-bar menu). */
function enableLoginItemOnFirstRun(): void {
  if (!app.isPackaged) return
  const marker = join(app.getPath('userData'), 'login-item-configured')
  if (existsSync(marker)) return
  app.setLoginItemSettings({ openAtLogin: true })
  writeFileSync(marker, new Date().toISOString())
}

app.setName('MCP Guard')

// One instance only: a second copy would show duplicate popups.
if (!app.requestSingleInstanceLock()) {
  app.quit()
} else {
  app.on('second-instance', showMainWindow)

  app.on('child-process-gone', (_e, details) => {
    console.error(`[mcp-guard] child process gone: ${details.type} (${details.reason}, exit ${details.exitCode})`)
  })

  app.whenReady().then(() => {
    enableLoginItemOnFirstRun()
    registerIpc()
    createTray()
    mainWindow = createMainWindow()

    guard.on('state', (state: GuardState) => {
      broadcast('guard:state', state)
      updateTray(state)
      if (state.pending.length === 0 && popupWindow && !popupWindow.isDestroyed()) popupWindow.hide()
    })
    guard.on('event', (event: string) => broadcast('guard:event', event))
    guard.on('created', (req: ToolRequest) => {
      showPopup()
      notify(req)
    })
    guard.start()

    app.on('activate', showMainWindow)
  })

  app.on('before-quit', () => {
    quitting = true
    guard.stop()
  })

  // Keep running in the menu bar when windows are closed.
  app.on('window-all-closed', () => {
    /* intentionally empty */
  })
}
