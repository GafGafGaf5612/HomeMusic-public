import { useEffect, useState } from 'react';
import type { FavoriteType } from '../api/types';
import { markLikePending, toggleLike, useLiked } from '../lib/stars';
import { IconButton, cx } from './ui';

/** Typed favorite toggle. A catalog track may download after ♥; album/artist favorites never download it.
 * `pending` is a legacy hint; an explicit own unlike always overrides it. */
export function LikeButton({
  songId,
  refId,
  pending,
  title,
  size = 20,
  className,
  targetType = 'track',
  ndId,
  data,
}: {
  songId?: string | null;
  refId?: string | null;
  pending?: boolean;
  title: string;
  size?: number;
  className?: string;
  targetType?: FavoriteType;
  ndId?: string | null;
  data?: Record<string, unknown>;
}) {
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (targetType === 'track' && pending && refId && !songId) markLikePending(refId);
  }, [pending, refId, songId, targetType]);
  const target = { targetType, ndId, songId, ref: refId, data };
  const liked = useLiked(target);
  if (!songId && !ndId && !refId) return null;
  return (
    <IconButton
      icon="heart"
      filled={liked}
      size={size}
      aria-pressed={liked}
      label={liked ? `Убрать «${title}» из понравившихся` : `«${title}» — в понравившиеся`}
      disabled={busy}
      onClick={async () => {
        setBusy(true);
        try { await toggleLike(target); } finally { setBusy(false); }
      }}
      className={cx(liked ? '!text-accent' : 'text-muted hover:text-fg', className)}
    />
  );
}
