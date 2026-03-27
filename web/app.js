/* ── State ─────────────────────────────────────────────────────────────── */
let evtSource = null;
let armed = false;
let alertActive = false;
let mouseLocked = false;
let currentPlatform = null;   // set from first SSE event; null = unknown
let authMode = 'login';       // 'login' | 'setup'
let reconnectTimer = null;
let recoveringVisibility = false;
let pushMaintenanceTimer = null;

const PUSH_REFRESH_INTERVAL_MS = 6 * 60 * 60 * 1000;

/* ── DOM refs ──────────────────────────────────────────────────────────── */
const authModal     = document.getElementById('auth-modal');
const authTitle     = document.getElementById('auth-title');
const authDesc      = document.getElementById('auth-desc');
const authSetupToken = document.getElementById('auth-setup-token');
const authUsername  = document.getElementById('auth-username');
const authPassword  = document.getElementById('auth-password');
const authPassword2 = document.getElementById('auth-password-confirm');
const authError     = document.getElementById('auth-error');
const btnAuthSubmit = document.getElementById('btn-auth-submit');
const alertBanner   = document.getElementById('alert-banner');
const btnArm        = document.getElementById('btn-arm');
const statusInd     = document.getElementById('status-indicator');
const statusLabel   = document.getElementById('status-label');
const statusSub     = document.getElementById('status-sublabel');
const connDot       = document.getElementById('conn-dot');
const connText      = document.getElementById('conn-text');
const btnMouseLock  = document.getElementById('btn-mouse-lock');
const btnLockScreen = document.getElementById('btn-lock-screen');
const btnShutdown   = document.getElementById('btn-shutdown');
const btnRestart    = document.getElementById('btn-restart');
const btnMessage    = document.getElementById('btn-message');
const btnLaunch     = document.getElementById('btn-launch');
const btnInstall    = document.getElementById('btn-install');
const msgModal      = document.getElementById('msg-modal');
const msgInput      = document.getElementById('msg-input');
const launchModal   = document.getElementById('launch-modal');
const launchInput   = document.getElementById('launch-input');
const platformBadge = document.getElementById('platform-badge');
const msgDesc       = document.getElementById('msg-desc');
const launchDesc    = document.getElementById('launch-desc');
let deferredInstallPrompt = null;

/* ── Platform-aware UI ─────────────────────────────────────────────────── */
function applyPlatform(platform) {
  currentPlatform = platform;
  const isWindows = platform === 'Windows';

  // Header badge
  if (platformBadge) {
    platformBadge.textContent = isWindows ? '🪟 Windows' : '🐧 Linux';
    platformBadge.classList.remove('hidden');
  }

  // Launch dialog: OS-appropriate placeholder and hint
  if (launchInput) {
    launchInput.placeholder = isWindows
      ? 'e.g. notepad.exe or "C:\\Program Files\\app.exe"'
      : 'e.g. firefox or /usr/bin/gedit';
  }
  if (launchDesc) {
    launchDesc.textContent = isWindows
      ? 'Run a program or command on the PC.'
      : 'Run a command or application on the PC.';
  }

  // Message dialog: OS-appropriate description
  if (msgDesc) {
    msgDesc.textContent = isWindows
      ? 'Shows a pop-up message box on the PC screen.'
      : 'Shows a desktop notification on the PC.';
  }
}

/* ── Auth modal ────────────────────────────────────────────────────────── */
function setAuthMode(mode, errorText = '') {
  authMode = mode;
  authError.textContent = errorText;
  authError.classList.toggle('hidden', !errorText);

  if (mode === 'setup') {
    authTitle.textContent = '🦎 Create Owner Account';
    authDesc.textContent = 'First launch detected. Enter the setup code from the tray icon, then create owner credentials.';
    btnAuthSubmit.textContent = 'Create Account';
    authSetupToken.classList.remove('hidden');
    authPassword.setAttribute('autocomplete', 'new-password');
    authPassword2.classList.remove('hidden');
  } else {
    authTitle.textContent = '🦎 Sign In';
    authDesc.textContent = 'Enter your owner credentials.';
    btnAuthSubmit.textContent = 'Sign In';
    authSetupToken.classList.add('hidden');
    authPassword.setAttribute('autocomplete', 'current-password');
    authPassword2.classList.add('hidden');
  }
}

function showAuthModal(mode, errorText = '') {
  setAuthMode(mode, errorText);
  authModal.classList.remove('hidden');
  authUsername.focus();
}

function hideAuthModal() {
  authModal.classList.add('hidden');
  authError.classList.add('hidden');
  authError.textContent = '';
  authSetupToken.value = '';
  authPassword.value = '';
  authPassword2.value = '';
}

async function submitAuth() {
  const setupToken = authSetupToken.value.trim().replace(/\s+/g, '');
  const username = authUsername.value.trim();
  const password = authPassword.value;
  const password2 = authPassword2.value;

  if (authMode === 'setup' && !setupToken) {
    setAuthMode(authMode, 'Setup code is required.');
    return;
  }
  if (!username || !password) {
    setAuthMode(authMode, 'Username and password are required.');
    return;
  }
  if (authMode === 'setup' && password !== password2) {
    setAuthMode(authMode, 'Passwords do not match.');
    return;
  }

  const endpoint = authMode === 'setup' ? '/api/auth/setup' : '/api/auth/login';
  const body = { username, password };
  if (authMode === 'setup') body.setup_code = setupToken;
  const res = await fetch(endpoint, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'same-origin',
    body: JSON.stringify(body),
  });

  if (res.ok) {
    hideAuthModal();
    await connectAfterAuth();
    return;
  }

  if (res.status === 409 && authMode === 'setup') {
    showAuthModal('login', 'Owner account already exists. Sign in.');
    return;
  }
  if (res.status === 401 && authMode === 'setup') {
    showAuthModal('setup', 'Invalid setup code. Right-click the tray icon → Show Setup Code.');
    return;
  }
  if (res.status === 400 && authMode === 'login') {
    showAuthModal('setup', 'No owner account found. Complete first-time setup.');
    return;
  }
  if (res.status === 429) {
    showAuthModal(authMode, 'Too many attempts. Wait a moment and try again.');
    return;
  }
  showAuthModal(authMode, authMode === 'setup' ? 'Could not create account.' : 'Invalid credentials.');
}

btnAuthSubmit.addEventListener('click', submitAuth);
[authUsername, authPassword, authPassword2].forEach(el => {
  el.addEventListener('keydown', e => { if (e.key === 'Enter') submitAuth(); });
});
authSetupToken.addEventListener('keydown', e => { if (e.key === 'Enter') submitAuth(); });

/* ── Push notification enrollment ──────────────────────────────────────── */
let pushEnabled = false;
let pushSubscription = null;

async function enrollPushNotifications(forceSubscribe = false) {
  if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
    console.log('[LZRD] Push notifications not supported');
    return;
  }
  if (!window.isSecureContext) {
    console.log('[LZRD] Push notifications require secure context (HTTPS)');
    return;
  }
  if (Notification.permission === 'denied') {
    console.log('[LZRD] Push notifications denied by user');
    return;
  }

  try {
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.getSubscription();
    if (sub) {
      pushSubscription = sub;
      pushEnabled = true;
      await registerPushSubscription(sub);
      console.log('[LZRD] Push already enrolled');
      return;
    }

    if (Notification.permission !== 'granted' && !forceSubscribe) {
      return;
    }

    const statusRes = await fetch('/api/push/status', { credentials: 'same-origin' });
    if (!statusRes.ok) {
      console.warn('[LZRD] Push status probe failed:', statusRes.status);
      return;
    }
    const statusData = await statusRes.json();
    if (!statusData.push_enabled || !statusData.vapid_public_key) {
      console.log('[LZRD] Server does not have Web Push configured');
      return;
    }

    try {
      const urlBase64ToUint8Array = (base64String) => {
        const padding = '='.repeat((4 - base64String.length % 4) % 4);
        const base64 = (base64String + padding)
          .replace(/\-/g, '+')
          .replace(/_/g, '/');
        return new Uint8Array(atob(base64).split('').map(c => c.charCodeAt(0)));
      };

      const sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(statusData.vapid_public_key)
      });
      pushSubscription = sub;
      pushEnabled = true;
      await registerPushSubscription(sub);
      console.log('[LZRD] Push enrollment successful');
    } catch (e) {
      console.warn('[LZRD] Push subscription failed:', e);
    }
  } catch (e) {
    console.warn('[LZRD] Push enrollment error:', e);
  }
}

async function registerPushSubscription(subscription) {
  try {
    await fetch('/api/push/subscribe', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({ subscription: subscription.toJSON() })
    });
  } catch (e) {
    console.warn('[LZRD] Could not register subscription:', e);
  }
}

function startPushMaintenance() {
  if (pushMaintenanceTimer) return;
  pushMaintenanceTimer = setInterval(() => {
    enrollPushNotifications(false).catch(() => {});
  }, PUSH_REFRESH_INTERVAL_MS);
}



/* ── SSE connection ────────────────────────────────────────────────────── */
function connect() {
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  if (evtSource) evtSource.close();
  setConn('connecting');

  evtSource = new EventSource('/api/events');

  evtSource.onopen = () => setConn('connected');

  evtSource.onmessage = e => {
    try { handleEvent(JSON.parse(e.data)); } catch (err) { console.warn('[LZRD] SSE parse error:', err); }
  };

  evtSource.onerror = () => {
    setConn('disconnected');
    evtSource.close();
    evtSource = null;
    handleDisconnect();
  };
}

async function handleDisconnect() {
  const probe = await probeStatus();
  if (!probe.ok && probe.status === 401) {
    showAuthModal('login', 'Session expired. Sign in again.');
    return;
  }
  if (document.visibilityState !== 'visible') {
    return;
  }
  reconnectTimer = setTimeout(() => {
    recoverConnectionNow('disconnect-timer').catch(() => {});
  }, 5000);
}

async function recoverConnectionNow(reason = 'manual') {
  if (recoveringVisibility) return;
  recoveringVisibility = true;
  try {
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    if (evtSource) {
      evtSource.close();
      evtSource = null;
    }

    const status = await probeStatus();
    if (!status.ok && status.status === 401) {
      showAuthModal('login', 'Session expired. Sign in again.');
      return;
    }
    if (status.ok && status.data) {
      handleEvent({ type: 'state', ...status.data });
    }
    connect();
    enrollPushNotifications(false).catch(() => {});
  } catch (err) {
    console.warn(`[LZRD] Recover failed (${reason}):`, err);
  } finally {
    recoveringVisibility = false;
  }
}

function setConn(state) {
  connDot.className = 'conn-dot ' + state;
  connText.textContent = state === 'connected' ? 'Connected' : state === 'connecting' ? 'Connecting…' : 'Disconnected';
}

/* ── Event handler ─────────────────────────────────────────────────────── */
function handleEvent(data) {
  if (data.type === 'alert') {
    if (navigator.vibrate) navigator.vibrate([200, 100, 200]);
    if ('Notification' in window && Notification.permission === 'granted') {
      try {
        new Notification('LZRD Alert', {
          body: 'Movement detected!',
          icon: '/icons/icon-192.png',
          badge: '/badge-icon.png',
          tag: 'lzrd-alert'
        });
      } catch (err) {
        console.warn('[LZRD] Notification failed:', err);
      }
    }
  }
  if (data.platform     !== undefined) applyPlatform(data.platform);
  if (data.armed        !== undefined) armed       = data.armed;
  if (data.alert        !== undefined) alertActive = data.alert;
  if (data.mouse_locked !== undefined) mouseLocked = data.mouse_locked;
  updateUI();
}

/* ── UI update ─────────────────────────────────────────────────────────── */
function updateUI() {
  if (armed) {
    statusInd.className = alertActive ? 'status-indicator alert' : 'status-indicator armed';
    statusLabel.textContent = alertActive ? '⚠️  Movement Detected' : 'Armed';
    statusSub.textContent   = alertActive ? 'Someone touched the mouse!' : 'Watching for movement…';
    btnArm.textContent = 'Disarm';
    btnArm.classList.add('armed');
  } else {
    statusInd.className = 'status-indicator';
    statusLabel.textContent = 'Disarmed';
    statusSub.textContent   = 'Tap Arm to activate tripwire';
    btnArm.textContent = 'Arm';
    btnArm.classList.remove('armed');
  }

  alertActive ? alertBanner.classList.add('visible') : alertBanner.classList.remove('visible');

  const lbl = btnMouseLock.querySelector('.btn-label');
  if (mouseLocked) {
    btnMouseLock.classList.add('active-state');
    lbl.textContent = 'Unlock Mouse';
  } else {
    btnMouseLock.classList.remove('active-state');
    btnMouseLock.blur();
    lbl.textContent = 'Lock Mouse';
  }
}

/* ── API helper ────────────────────────────────────────────────────────── */
async function api(path, body) {
  try {
    const opts = {
      method: body !== undefined ? 'POST' : 'GET',
      headers: {},
      credentials: 'same-origin'
    };
    if (body !== undefined) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
    const res = await fetch(path, opts);
    if (res.status === 401) {
      if (evtSource) {
        evtSource.close();
        evtSource = null;
      }
      setConn('disconnected');
      showAuthModal('login', 'Sign in required.');
      return null;
    }
    if (res.status === 429) {
      showToast('Too many requests. Please wait a moment.', 'error');
      return null;
    }
    const data = await res.json();
    if (!res.ok) {
      showToast(data.error || 'Request failed', 'error');
      return null;
    }
    return data;
  } catch (err) {
    showToast('Network error', 'error');
    return null;
  }
}

async function probeStatus() {
  try {
    const res = await fetch('/api/status', {
      method: 'GET',
      headers: {},
      credentials: 'same-origin'
    });
    const data = res.ok ? await res.json() : null;
    return { ok: res.ok, status: res.status, data };
  } catch (err) {
    return { ok: false, status: 0, data: null };
  }
}

async function connectAfterAuth() {
  const status = await probeStatus();
  if (!status.ok) {
    showAuthModal('login', status.status === 429 ? 'Temporarily rate limited. Try again shortly.' : 'Sign in required.');
    return;
  }
  handleEvent({ type: 'state', ...status.data });
  connect();
  enrollPushNotifications(true).catch(() => {});
  startPushMaintenance();
}

async function initAuthFlow() {
  // Cleanup legacy token storage now that sessions are preferred.
  localStorage.removeItem('lzrd_token');

  let bootstrap;
  try {
    const res = await fetch('/api/auth/bootstrap-status', { credentials: 'same-origin' });
    bootstrap = await res.json();
  } catch (err) {
    showToast('Cannot reach server', 'error');
    showAuthModal('login', 'Cannot reach server.');
    return;
  }

  if (bootstrap.requires_setup) {
    showAuthModal('setup');
    return;
  }

  const status = await probeStatus();
  if (!status.ok) {
    showAuthModal('login');
    return;
  }
  handleEvent({ type: 'state', ...status.data });
  hideAuthModal();
  connect();
  enrollPushNotifications(true).catch(() => {});
  startPushMaintenance();
}

/* ── Button press flash ────────────────────────────────────────────────── */
function flashBtn(btn) {
  btn.blur();
  btn.classList.add('press-flash');
  setTimeout(() => btn.classList.remove('press-flash'), 200);
}

/* ── Button handlers ───────────────────────────────────────────────────── */
btnArm.addEventListener('click', () => api(armed ? '/api/disarm' : '/api/arm', {}));

btnLockScreen.addEventListener('click', async () => {
  flashBtn(btnLockScreen);
  const r = await api('/api/lock-screen', {});
  if (r?.ok) showToast('Screen locked', 'success');
});

btnMouseLock.addEventListener('click', async () => {
  btnMouseLock.blur();
  const r = await api('/api/lock-mouse', {});
  if (r?.ok) showToast(r.mouse_locked ? 'Mouse locked' : 'Mouse unlocked', 'success');
});

btnShutdown.addEventListener('click', async () => {
  flashBtn(btnShutdown);
  if (!confirm('⚠️  Shut down the computer?')) return;
  const r = await api('/api/shutdown', {});
  if (r?.ok) showToast(currentPlatform === 'Windows' ? 'Shutdown in 5 s…' : 'Shutting down…', 'success');
});

btnRestart.addEventListener('click', async () => {
  flashBtn(btnRestart);
  if (!confirm('🔄  Restart the computer?')) return;
  const r = await api('/api/restart', {});
  if (r?.ok) showToast(currentPlatform === 'Windows' ? 'Restart in 5 s…' : 'Restarting…', 'success');
});

/* ── Message dialog ────────────────────────────────────────────────────── */
btnMessage.addEventListener('click', () => { flashBtn(btnMessage); msgModal.classList.remove('hidden'); msgInput.focus(); });
document.getElementById('btn-msg-cancel').addEventListener('click', () => msgModal.classList.add('hidden'));
document.getElementById('btn-msg-send').addEventListener('click', async () => {
  const text = msgInput.value.trim();
  if (!text) return;
  msgModal.classList.add('hidden');
  msgInput.value = '';
  const r = await api('/api/message', { text });
  if (r?.ok) showToast('Message sent', 'success');
});

/* ── Launch app dialog ─────────────────────────────────────────────────── */
btnLaunch.addEventListener('click', () => { flashBtn(btnLaunch); launchModal.classList.remove('hidden'); launchInput.focus(); });
document.getElementById('btn-launch-cancel').addEventListener('click', () => launchModal.classList.add('hidden'));
document.getElementById('btn-launch-go').addEventListener('click', async () => {
  const path = launchInput.value.trim();
  if (!path) return;
  launchModal.classList.add('hidden');
  launchInput.value = '';
  const r = await api('/api/launch', { path });
  if (r?.ok) showToast('Application launched', 'success');
});

/* ── Toast ─────────────────────────────────────────────────────────────── */
function showToast(msg, type = '') {
  const el = Object.assign(document.createElement('div'), { className: `toast ${type}`, textContent: msg });
  document.getElementById('toast-container').appendChild(el);
  requestAnimationFrame(() => requestAnimationFrame(() => el.classList.add('visible')));
  setTimeout(() => { el.classList.remove('visible'); setTimeout(() => el.remove(), 300); }, 2500);
}

function _logInstallReadiness(reason) {
  console.log('[LZRD] install readiness', {
    reason,
    secure: window.isSecureContext,
    standalone: _isStandaloneDisplayMode(),
    hasDeferredPrompt: Boolean(deferredInstallPrompt),
    swController: Boolean(navigator.serviceWorker && navigator.serviceWorker.controller),
  });
}

/* ── Service worker ────────────────────────────────────────────────────── */
const _swPageLoadTime = Date.now();
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.addEventListener('controllerchange', () => {
    if (window.__lzrdSwRefreshing) return;
    // Suppress reload if controller changed within the first 5 s of page load —
    // this is the SW activating on first install, not a genuine update. Reloading
    // here races with the browser's PWA install-criteria check and kills the
    // beforeinstallprompt event.
    if (Date.now() - _swPageLoadTime < 5000) return;
    window.__lzrdSwRefreshing = true;
    window.location.reload();
  });

  navigator.serviceWorker.register('/sw.js').then(reg => {
    console.log('[LZRD] Service worker ready:', reg.scope);
    reg.update().catch(() => {});
    setInterval(() => reg.update().catch(() => {}), 60 * 1000);

    // If this page is not yet controlled by a SW, trigger a one-time warmup
    // reload to make installability checks available sooner.
    if (!navigator.serviceWorker.controller) {
      const warmupFlag = 'lzrd_sw_warmup_reload_done';
      if (!sessionStorage.getItem(warmupFlag)) {
        sessionStorage.setItem(warmupFlag, '1');
        setTimeout(() => window.location.reload(), 300);
      }
    } else {
      sessionStorage.removeItem('lzrd_sw_warmup_reload_done');
    }
  }).catch(err => {
    console.warn('[LZRD] Service worker registration failed:', err);
  });
}

function _isStandaloneDisplayMode() {
  return window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true;
}

function _updateInstallButtonVisibility() {
  if (!btnInstall) return;
  const visible = !_isStandaloneDisplayMode() && window.isSecureContext && ('serviceWorker' in navigator);
  btnInstall.classList.toggle('hidden', !visible);
  btnInstall.textContent = 'Install App';
  btnInstall.disabled = !deferredInstallPrompt;
  btnInstall.title = deferredInstallPrompt
    ? 'Install LZRD app'
    : 'Install not ready yet - keep the page open briefly and interact once';
}

window.addEventListener('beforeinstallprompt', (e) => {
  e.preventDefault();
  deferredInstallPrompt = e;
  _updateInstallButtonVisibility();
  _logInstallReadiness('beforeinstallprompt');
  console.log('[LZRD] beforeinstallprompt fired');
});

window.addEventListener('appinstalled', () => {
  deferredInstallPrompt = null;
  _updateInstallButtonVisibility();
  _logInstallReadiness('appinstalled');
  console.log('[LZRD] appinstalled fired');
});

if (btnInstall) {
  btnInstall.addEventListener('click', async () => {
    if (!deferredInstallPrompt) {
      _logInstallReadiness('install-click-not-ready');
      showToast('Install is not ready yet. Stay on this page for a bit and tap once, then retry.', 'error');
      return;
    }
    deferredInstallPrompt.prompt();
    try {
      const choice = await deferredInstallPrompt.userChoice;
      console.log('[LZRD] install choice:', choice?.outcome || 'unknown');
    } catch {
      // Some browsers do not expose userChoice consistently.
    }
    deferredInstallPrompt = null;
    _updateInstallButtonVisibility();
  });
}

/* ── Visibility restore ────────────────────────────────────────────────── */
document.addEventListener('visibilitychange', async () => {
  if (document.visibilityState !== 'visible') return;
  recoverConnectionNow('visibility').catch(() => {});
});

window.addEventListener('pageshow', () => {
  recoverConnectionNow('pageshow').catch(() => {});
  _updateInstallButtonVisibility();
});

window.addEventListener('focus', () => {
  recoverConnectionNow('focus').catch(() => {});
  _updateInstallButtonVisibility();
});

window.addEventListener('online', () => {
  recoverConnectionNow('online').catch(() => {});
  enrollPushNotifications(false).catch(() => {});
});

/* ── Init ──────────────────────────────────────────────────────────────── */
_updateInstallButtonVisibility();
_logInstallReadiness('init');
initAuthFlow();
