import { create } from 'zustand'

interface SessionState {
  authenticated: boolean
  expiresAt: number | null
  error: string | null
  setAuthenticated: (expiresAt: number) => void
  setError: (error: string | null) => void
  logout: () => void
}

export const useSessionStore = create<SessionState>((set) => ({
  authenticated: false,
  expiresAt: null,
  error: null,
  setAuthenticated: (expiresAt) => set({ authenticated: true, expiresAt, error: null }),
  setError: (error) => set({ error }),
  logout: () => set({ authenticated: false, expiresAt: null }),
}))
