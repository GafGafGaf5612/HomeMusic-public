import { useEffect, useState, type FormEvent } from 'react';
import { api, errorMessage } from '../api/client';
import type { MeOut } from '../api/types';
import { Button, Field, Notice } from '../components/ui';

// register — by an invite code; email — by a code from a letter, no invite needed
type Mode = 'login' | 'register' | 'email';

const SUBTITLE: Record<Mode, string> = {
  login: 'Войдите со своим именем и паролем',
  register: 'Новый аккаунт по коду приглашения',
  email: 'Новый аккаунт по почте',
};

export function LoginPage({ onLogin }: { onLogin: (me: MeOut) => void }) {
  const [mode, setMode] = useState<Mode>('login');
  const [emailSignup, setEmailSignup] = useState(false);
  const [code, setCode] = useState('');
  const [email, setEmail] = useState('');
  // the address the letter went to: the rest of the form shows once it is sent
  const [sentTo, setSentTo] = useState<string>();
  const [resendAt, setResendAt] = useState(0);
  const [now, setNow] = useState(() => Date.now());
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [password2, setPassword2] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  useEffect(() => {
    const ctl = new AbortController();
    api.authOptions(ctl.signal).then((o) => setEmailSignup(!!o.email_signup), () => {});
    return () => ctl.abort();
  }, []);

  useEffect(() => {
    if (resendAt <= now) return;
    const t = window.setTimeout(() => setNow(Date.now()), 1000);
    return () => window.clearTimeout(t);
  }, [resendAt, now]);

  const switchTo = (m: Mode) => {
    setMode(m);
    setError(undefined);
    setCode('');
    setPassword('');
    setPassword2('');
  };

  const sendLetter = async () => {
    setBusy(true);
    setError(undefined);
    try {
      const out = await api.emailCode({ email: email.trim() });
      setSentTo(email.trim());
      setCode('');
      setResendAt(Date.now() + out.resend_in * 1000);
      setNow(Date.now());
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const awaitingLetter = mode === 'email' && sentTo === undefined;
  const newAccount = mode !== 'login';
  const mismatch = newAccount && password2 !== '' && password !== password2;
  const ready = awaitingLetter
    ? !!email.trim()
    : !!username.trim() && !!password && (mode === 'login' || (!!code.trim() && password === password2));

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!ready) return;
    if (awaitingLetter) return void sendLetter();
    setBusy(true);
    setError(undefined);
    try {
      const body = { username: username.trim(), password };
      onLogin(
        mode === 'login'
          ? await api.login(body)
          : mode === 'register'
            ? await api.register({ ...body, code: code.trim() })
            : await api.emailRegister({ ...body, code: code.trim(), email: sentTo ?? email.trim() }),
      );
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const plain = { autoCapitalize: 'none', autoCorrect: 'off', spellCheck: false } as const;
  const wait = Math.max(0, Math.ceil((resendAt - now) / 1000));

  return (
    <div className="pt-safe flex min-h-dvh flex-col justify-center bg-gradient-to-b from-[#123a24] via-bg to-bg px-6">
      <div className="mx-auto w-full max-w-sm">
        <div className="mb-8 flex flex-col items-center gap-3 text-center">
          <img src="/icons/icon.svg" alt="" width={80} height={80} className="rounded-2xl shadow-lg" />
          <h1 className="text-3xl font-bold">HomeMusic</h1>
          <p className="text-sm text-muted">{SUBTITLE[mode]}</p>
        </div>
        <form onSubmit={submit} className="space-y-4">
          {mode === 'register' && (
            <Field label="Код приглашения">
              <input
                className="input font-mono uppercase tracking-widest"
                placeholder="ABCD-EFGH"
                autoComplete="one-time-code"
                {...plain}
                value={code}
                onChange={(e) => setCode(e.target.value)}
                required
              />
            </Field>
          )}
          {mode === 'email' && (
            <Field label="Почта">
              <input
                className="input"
                type="email"
                inputMode="email"
                autoComplete="email"
                placeholder="name@example.com"
                {...plain}
                value={email}
                readOnly={sentTo !== undefined}
                onChange={(e) => setEmail(e.target.value)}
                required
              />
            </Field>
          )}
          {mode === 'email' && sentTo !== undefined && (
            <>
              <p className="text-sm text-muted">
                Письмо с кодом отправлено на <span className="font-semibold text-fg">{sentTo}</span>. Если его нет, загляните
                в «Спам».
              </p>
              <Field label="Код из письма">
                <input
                  className="input font-mono tracking-[0.4em]"
                  placeholder="000000"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  maxLength={12}
                  {...plain}
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                  required
                />
              </Field>
              <div className="flex flex-wrap gap-x-4">
                <button
                  type="button"
                  className="min-h-11 text-sm font-semibold text-accent disabled:text-dim"
                  disabled={busy || wait > 0}
                  onClick={sendLetter}
                >
                  {wait > 0 ? `Отправить ещё раз через ${wait} с` : 'Отправить ещё раз'}
                </button>
                <button
                  type="button"
                  className="min-h-11 text-sm font-semibold text-accent"
                  onClick={() => {
                    setSentTo(undefined);
                    setError(undefined);
                  }}
                >
                  Другая почта
                </button>
              </div>
            </>
          )}
          {!awaitingLetter && (
            <>
              <Field label={mode === 'login' ? 'Имя или почта' : 'Имя'}>
                <input className="input" autoComplete="username" {...plain} value={username} onChange={(e) => setUsername(e.target.value)} required />
              </Field>
              <Field label="Пароль">
                <input
                  className="input"
                  type="password"
                  autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                />
              </Field>
              {newAccount && (
                <Field label="Пароль ещё раз">
                  <input className="input" type="password" autoComplete="new-password" value={password2} onChange={(e) => setPassword2(e.target.value)} required />
                </Field>
              )}
            </>
          )}
          {mismatch && <Notice kind="error">Пароли не совпадают</Notice>}
          {error && <Notice kind="error">{error}</Notice>}
          <Button type="submit" variant="primary" block busy={busy} disabled={!ready}>
            {mode === 'login' ? 'Войти' : awaitingLetter ? 'Получить код' : 'Создать аккаунт'}
          </Button>
        </form>
        <div className="mt-6 flex flex-col items-center">
          {mode === 'login' ? (
            <>
              {emailSignup && (
                <button type="button" className="min-h-11 text-sm font-semibold text-accent" onClick={() => switchTo('email')}>
                  Зарегистрироваться по почте
                </button>
              )}
              <button type="button" className="min-h-11 text-sm font-semibold text-accent" onClick={() => switchTo('register')}>
                У меня есть код приглашения
              </button>
            </>
          ) : (
            <button type="button" className="min-h-11 text-sm font-semibold text-accent" onClick={() => switchTo('login')}>
              У меня уже есть аккаунт
            </button>
          )}
        </div>
        <p className="mt-2 text-center text-xs text-dim">
          {mode === 'login'
            ? emailSignup
              ? 'Аккаунт создаётся по почте или по приглашению администратора. Те же имя и пароль подходят для плеера.'
              : 'Аккаунт создаётся по приглашению: код даёт администратор. Те же имя и пароль подходят для плеера.'
            : awaitingLetter
              ? 'Пришлём на почту код из 6 цифр: он подтвердит, что адрес ваш.'
              : 'Имя — латиницей или по-русски, без пробелов; пароль — не короче 6 символов.'}
        </p>
      </div>
    </div>
  );
}
