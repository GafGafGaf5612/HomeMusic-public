import { dismissToast, useDialog, useToasts } from '../lib/ui';
import { navigate } from '../lib/router';
import { Button, cx } from './ui';
import { Icon } from './Icon';

/** Toast stack (above the tab bar) and the global confirm dialog. */
export function Overlays() {
  const toasts = useToasts();
  const dialog = useDialog();
  return (
    <>
      <div className="pointer-events-none fixed inset-x-0 bottom-[calc(var(--tabbar-h)+var(--player-h,0px)+12px+env(safe-area-inset-bottom))] z-[60] flex flex-col items-center gap-2 px-3" aria-live="polite">
        {toasts.map((t) => (
          <div
            key={t.id}
            className={cx(
              'anim-up pointer-events-auto flex w-full max-w-md items-center gap-3 rounded-xl px-4 py-3 text-sm shadow-xl',
              t.kind === 'error' ? 'bg-[#3a1715] text-[#ffd9d6]' : t.kind === 'success' ? 'bg-[#123321] text-[#c8f7d8]' : 'bg-[#2a2a2a] text-fg',
            )}
            role={t.kind === 'error' ? 'alert' : 'status'}
          >
            <Icon name={t.kind === 'error' ? 'alert' : t.kind === 'success' ? 'check' : 'bell'} size={18} className="shrink-0" />
            <span className="min-w-0 flex-1">{t.text}</span>
            {t.link && (
              <button
                type="button"
                className="shrink-0 font-semibold text-accent"
                onClick={() => {
                  dismissToast(t.id);
                  if (t.link!.run) t.link!.run();
                  else if (t.link!.to) navigate(t.link!.to);
                }}
              >
                {t.link.label}
              </button>
            )}
            <button type="button" aria-label="Скрыть" className="-mr-1 shrink-0 p-1 text-muted" onClick={() => dismissToast(t.id)}>
              <Icon name="x" size={16} />
            </button>
          </div>
        ))}
      </div>

      {dialog && (
        <div className="fixed inset-0 z-[70] flex items-center justify-center p-5" role="alertdialog" aria-modal="true" aria-labelledby="hm-dialog-title">
          <button type="button" aria-label="Отмена" className="absolute inset-0 bg-black/70" onClick={() => dialog.resolve(false)} />
          <div className="anim-up relative w-full max-w-sm rounded-2xl bg-surface p-5">
            <h2 id="hm-dialog-title" className="text-lg font-bold">
              {dialog.title}
            </h2>
            {dialog.body && <p className="mt-2 whitespace-pre-line text-sm text-muted">{dialog.body}</p>}
            <div className="mt-5 flex justify-end gap-2">
              <Button variant="ghost" onClick={() => dialog.resolve(false)}>
                {dialog.cancelLabel}
              </Button>
              <Button variant={dialog.danger ? 'danger' : 'primary'} onClick={() => dialog.resolve(true)} autoFocus>
                {dialog.okLabel}
              </Button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
