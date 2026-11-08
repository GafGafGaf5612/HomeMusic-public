import { createContext, useContext } from 'react';
import type { MeOut } from '../api/types';

export interface Session {
  me: MeOut;
  logout: () => Promise<void>;
}

export const SessionContext = createContext<Session | null>(null);

export function useSession(): Session {
  const s = useContext(SessionContext);
  if (!s) throw new Error('useSession outside of a session');
  return s;
}
