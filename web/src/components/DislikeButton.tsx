import { dislikeAndSkip, setDisliked, useDisliked } from '../lib/dislikes';
import { IconButton, cx } from './ui';

/** 👎 next to ♥ in the players: «Не нравится» for the playing track and on to the next one (MIXES.md 3.2); pressed
 * again (a track marked before), it takes the mark off. */
export function DislikeButton({
  track,
  size = 20,
  className,
}: {
  track: { ref?: string | null; nd_song_id?: string | null; artist?: string | null; title: string };
  size?: number;
  className?: string;
}) {
  const target = { ref: track.ref, nd_song_id: track.nd_song_id, artist: track.artist, title: track.title };
  const disliked = useDisliked(target);
  // a provider's file (`file:…`) is no catalog track: nothing to keep out of the mixes
  if (!track.nd_song_id && (!track.ref || track.ref.startsWith('file:'))) return null;
  return (
    <IconButton
      icon="dislike"
      size={size}
      aria-pressed={disliked}
      label={disliked ? `Убрать «Не нравится» с «${track.title}»` : `Не нравится — следующий трек`}
      onClick={() => void (disliked ? setDisliked(target, false) : dislikeAndSkip(target))}
      className={cx(disliked ? '!text-accent' : 'text-muted hover:text-fg', className)}
    />
  );
}
