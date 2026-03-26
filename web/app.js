/* ── State ─────────────────────────────────────────────────────────────── */
let token = localStorage.getItem('lzrd_token') || '';
let evtSource = null;
let armed = false;
let alertActive = false;
let mouseLocked = false;

/* ── DOM refs ──────────────────────────────────────────────────────────── */
const tokenModal    = document.getElementById('token-modal');
const tokenInput    = document.getElementById('token-input');
const alertBanner   = document.getElementById('alert-banner');
const btnArm        = document.getElementById('btn-arm');
const statusInd     = document.getElementById('status-indicator');
const statusLabel   = document.getElementById('status-label');
const statusSub     = document.getElementById('status-sublabel');
const connDot       = document.getElementById('conn-dot');
const connText      = document.getElementById('conn-text');
const btnMouseLock  = document.getElementById('btn-mouse-lock');
const msgModal      = document.getElementById('msg-modal');
const msgInput      = document.getElementById('msg-input');
const launchModal   = document.getElementById('launch-modal');
const launchInput   = document.getElementById('launch-input');

/* ── Token modal ───────────────────────────────────────────────────────── */
function showTokenModal() { tokenModal.classList.remove('hidden'); tokenInput.focus(); }
function hideTokenModal() { tokenModal.classList.add('hidden'); }

document.getElementById('btn-connect').addEventListener('click', () => {
  const t = tokenInput.value.trim();
  if (!t) return;
  token = t;
  localStorage.setItem('lzrd_token', token);
  hideTokenModal();
  connect();
});
tokenInput.addEventListener('keydown', e => { if (e.key === 'Enter') document.getElementById('btn-connect').click(); });

/* ── SSE connection ────────────────────────────────────────────────────── */
function connect() {
  if (evtSource) evtSource.close();
  setConn('connecting');

  evtSource = new EventSource(`/api/events?token=${encodeURIComponent(token)}`);

  evtSource.onopen = () => setConn('connected');

  evtSource.onmessage = e => {
    try { handleEvent(JSON.parse(e.data)); } catch (_) {}
  };

  evtSource.onerror = () => {
    setConn('disconnected');
    evtSource.close();
    evtSource = null;
    if (token) setTimeout(connect, 5000);
  };
}

function setConn(state) {
  connDot.className = 'conn-dot ' + state;
  connText.textContent = state === 'connected' ? 'Connected' : state === 'connecting' ? 'Connecting…' : 'Disconnected';
}

/* ── Event handler ─────────────────────────────────────────────────────── */
function handleEvent(data) {
  if (data.type === 'alert' && navigator.vibrate) navigator.vibrate([200, 100, 200]);
  if (data.armed      !== undefined) armed       = data.armed;
  if (data.alert      !== undefined) alertActive = data.alert;
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
    lbl.textContent = 'Lock Mouse';
  }
}

/* ── API helper ────────────────────────────────────────────────────────── */
async function api(path, body) {
  try {
    const opts = { method: body !== undefined ? 'POST' : 'GET', headers: { 'X-Token': token } };
    if (body !== undefined) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
    const res = await fetch(path, opts);
    if (res.status === 401) { showToast('Access denied — check your token', 'error'); showTokenModal(); return null; }
    return res.json();
  } catch (err) {
    showToast('Network error', 'error');
    return null;
  }
}

/* ── Button handlers ───────────────────────────────────────────────────── */
btnArm.addEventListener('click', () => api(armed ? '/api/disarm' : '/api/arm', {}));

async function lockScreen() {
  const r = await api('/api/lock-screen', {});
  if (r?.ok) showToast('Screen locked', 'success');
}

async function toggleMouseLock() {
  const r = await api('/api/lock-mouse', {});
  if (r?.ok) showToast(r.mouse_locked ? 'Mouse locked' : 'Mouse unlocked', 'success');
}

async function confirmShutdown() {
  if (!confirm('⚠️  Shut down the computer?')) return;
  const r = await api('/api/shutdown', {});
  if (r?.ok) showToast('Shutdown in 5 s…', 'success');
}

async function confirmRestart() {
  if (!confirm('🔄  Restart the computer?')) return;
  const r = await api('/api/restart', {});
  if (r?.ok) showToast('Restart in 5 s…', 'success');
}

/* ── Message dialog ────────────────────────────────────────────────────── */
function showMessageDialog() { msgModal.classList.remove('hidden'); msgInput.focus(); }
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
function showLaunchDialog() { launchModal.classList.remove('hidden'); launchInput.focus(); }
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

/* ── Service worker ────────────────────────────────────────────────────── */
if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});

/* ── Init ──────────────────────────────────────────────────────────────── */
if (!token) showTokenModal(); else connect();
