// Handing text to another app (an invite code to a messenger): the Android app's share sheet (bridge
// `shareText`, app 1.3.0+), the browser's Web Share, or the clipboard.

interface ShareBridge {
  shareText?: (text: string) => void;
}

function bridge(): ShareBridge | undefined {
  return (window as unknown as { HomeMusicAndroid?: ShareBridge }).HomeMusicAndroid;
}

export function canShare(): boolean {
  return typeof bridge()?.shareText === 'function' || typeof navigator.share === 'function';
}

/** Resolves false when the person closed the share sheet. */
export async function shareText(text: string): Promise<boolean> {
  const b = bridge();
  if (typeof b?.shareText === 'function') {
    b.shareText(text);
    return true;
  }
  try {
    await navigator.share({ text });
    return true;
  } catch {
    return false;
  }
}

/** Copies within the tap: the old way first — the app's WebView has no Clipboard API permission (its
 * `writeText` fails) and plain http has no Clipboard API at all; then the Clipboard API. */
export async function copyText(text: string): Promise<boolean> {
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.setAttribute('readonly', '');
  ta.style.position = 'fixed';
  ta.style.top = '0';
  ta.style.opacity = '0';
  document.body.appendChild(ta);
  ta.focus();
  ta.select();
  ta.setSelectionRange(0, text.length);
  let ok = false;
  try {
    ok = document.execCommand('copy');
  } catch {
    ok = false;
  }
  ta.remove();
  if (ok) return true;
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}
