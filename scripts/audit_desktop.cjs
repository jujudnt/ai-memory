// Exercise UI state in memory. No requests reach the running application.
const { JSDOM } = require('jsdom');
const fs = require('node:fs');
const path = require('node:path');
const assets = path.join(__dirname, '../src/aimemory/assets');
const dom = new JSDOM(fs.readFileSync(path.join(assets, 'desktop.html'), 'utf8'), {
  runScripts: 'outside-only', url: 'http://127.0.0.1/', pretendToBeVisual: true,
});
const w = dom.window;
w.lucide = { createIcons() {} };
w.fetch = () => new Promise(() => {});
w.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
w.HTMLDialogElement.prototype.close = function () { this.open = false; };
w.eval(fs.readFileSync(path.join(assets, 'desktop.js'), 'utf8'));
const el = id => w.document.getElementById(id);
const base = {
  storage: { provider: 'icloud-online', cloud_sync: 'configured', remote: 'AI-Memory' },
  health: { state: 'error', label: 'Collecte en erreur' },
  watcher: { running: true, last_error: 'Too many open files' },
  watcher_service: { installed: true }, recent_conversations: [],
  job: { running: true }, sync_active: true, sync: { status: 'downloading' },
};
w.render(base);
console.log(JSON.stringify({
  probe: 'recovery_during_sync',
  watcher_repair_hidden: el('watcher-enable').hidden,
  install_hidden: el('autostart-enable').hidden,
  folder_disabled: w.document.querySelector('[data-action="open-folder"]').disabled,
  disconnect_disabled: w.document.querySelector('[data-action="disconnect-cloud"]').disabled,
}));
w.render({ ...base, job: {}, icloud_auth: { status: 'needs_2fa' } });
el('provider').value = 'google-drive';
el('provider').dispatchEvent(new w.Event('change'));
const selected = el('provider').value;
w.render({ ...base, job: {}, icloud_auth: { status: 'needs_2fa' } });
console.log(JSON.stringify({
  probe: 'switch_away_from_pending_icloud',
  selected_provider: selected, provider_after_poll: el('provider').value,
}));
dom.window.close();
