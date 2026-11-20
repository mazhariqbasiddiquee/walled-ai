import type { GuardApi } from '../shared/types'

declare global {
  interface Window {
    guard: GuardApi
  }
}

export {}
