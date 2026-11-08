import type { HealthOut, PlayEventIn } from '../api/types';

let supported = false;
let checkpointsSupported = false;
let checkedAt = 0;
let checking: Promise<boolean | null> | null = null;

/** Unknown/offline servers are treated conservatively; a later request checks again. */
export async function typedFavoritesSupported(): Promise<boolean | null> {
  if (Date.now() - checkedAt < 30_000) return supported;
  if (checking) return checking;
  checking = (async () => {
    let result: boolean | null = null;
    try {
      const r = await fetch('/healthz', { cache: 'no-store', signal: AbortSignal.timeout(5000) });
      if (!r.ok) throw new Error('health');
      const caps = ((await r.json()) as HealthOut).capabilities ?? [];
      supported = caps.includes('typed_favorites_v1');
      checkpointsSupported = caps.includes('listening_checkpoints_v1');
      checkedAt = Date.now();
      result = supported;
    } catch {
      supported = false;
      checkpointsSupported = false;
    } finally {
      checking = null;
    }
    return result;
  })();
  return checking;
}

export function canSendPlayEvent(event: PlayEventIn): boolean {
  if (['progress', 'interrupted', 'error'].includes(event.kind) && !checkpointsSupported) return false;
  return supported || ((!event.target_type || event.target_type === 'track') && !event.reverts_event_id);
}
