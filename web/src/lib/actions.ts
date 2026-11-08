import { api } from '../api/client';
import type { DownloadBatchOut, DownloadIn } from '../api/types';
import { tracksWord } from './format';
import { attempt, toast } from './ui';

/** POST /api/downloads with a Russian summary toast. Returns undefined on error (already toasted). */
export async function startDownload(body: DownloadIn): Promise<DownloadBatchOut | undefined> {
  const r = await attempt(() => api.download(body));
  if (!r) return undefined;
  if (r.queued > 0) {
    const owned = r.already_owned ? `, уже есть: ${r.already_owned}` : '';
    toast(`В очереди: ${tracksWord(r.queued)}${owned}`, 'success', { label: 'Загрузки', to: '/downloads' });
  } else if (r.expanding) {
    toast('Добавлено в загрузки', 'success', { label: 'Загрузки', to: '/downloads' });
  } else if (r.already_owned > 0) {
    toast('Уже есть в библиотеке', 'info');
  } else {
    toast('Нечего скачивать', 'info');
  }
  return r;
}
