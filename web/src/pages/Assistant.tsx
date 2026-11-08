import { useEffect } from 'react';
import { api } from '../api/client';
import { Icon } from '../components/Icon';
import { Empty, ErrorState, IconButton, Loading, PageHeader, cx } from '../components/ui';
import { AssistantChat, currentChatId, forgetChat, newChat } from './AssistantChat';
import { assistantStatusLabel, fmtWhen } from '../lib/format';
import { useAsync } from '../lib/hooks';
import { Link, navigate } from '../lib/router';
import { attempt, confirmDialog } from '../lib/ui';

/** The assistant is a chat; `/assistant` without `?chat=` reopens the chat open last in this tab. */
export function AssistantPage({ chatId }: { chatId: number | null }) {
  const reopen = chatId ? null : currentChatId();

  useEffect(() => {
    if (reopen) navigate(`/assistant?chat=${reopen}`, { replace: true });
  }, [reopen]);

  return (
    <div>
      <PageHeader
        title="Ассистент"
        right={
          <>
            <IconButton icon="history" label="История чатов" onClick={() => navigate('/assistant/chats')} />
            <IconButton
              icon="plus"
              label="Новый чат"
              onClick={() => {
                newChat();
                navigate('/assistant');
              }}
            />
          </>
        }
      />
      {!reopen && <AssistantChat chatId={chatId} />}
    </div>
  );
}

export function AssistantChatsPage() {
  const chats = useAsync((s) => api.chats(s), []);

  const remove = async (id: number, title: string) => {
    if (!(await confirmDialog({ title: 'Удалить чат?', body: `«${title}» пропадёт из истории.`, okLabel: 'Удалить', danger: true }))) return;
    if (!(await attempt(() => api.deleteChat(id)))) return;
    forgetChat(id);
    chats.setData((prev) => prev?.filter((c) => c.id !== id));
  };

  return (
    <div>
      <PageHeader title="История чатов" back="/assistant" />
      {chats.loading ? (
        <Loading />
      ) : chats.error ? (
        <ErrorState text={chats.error} onRetry={() => chats.reload()} />
      ) : !chats.data?.length ? (
        <Empty icon="sparkle" title="Чатов пока нет" text="Переписка с ассистентом сохраняется здесь." />
      ) : (
        <ul className="divide-y divide-line/60">
          {chats.data.map((c) => (
            <li key={c.id} className="flex items-center pr-2 hover:bg-fg/5">
              <Link to={`/assistant?chat=${c.id}`} className="flex min-h-16 min-w-0 flex-1 items-center gap-3 px-4 py-2">
                <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-raised text-muted">
                  <Icon name="sparkle" size={20} />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate">{c.title || 'Без названия'}</span>
                  <span className="block truncate text-xs text-muted">{fmtWhen(c.updated_at)}</span>
                </span>
              </Link>
              <IconButton icon="trash" label="Удалить чат" className="!text-muted hover:!text-danger" onClick={() => remove(c.id, c.title || 'Без названия')} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function TaskStatus({ status }: { status: string }) {
  const color =
    status === 'awaiting_confirmation'
      ? 'bg-warn/15 text-warn'
      : status === 'done'
        ? 'bg-accent/15 text-accent'
        : status === 'failed'
          ? 'bg-danger/15 text-danger'
          : 'bg-info/15 text-info';
  return <span className={cx('shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium', color)}>{assistantStatusLabel(status)}</span>;
}
