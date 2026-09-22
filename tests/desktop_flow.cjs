const { JSDOM } = require('jsdom');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const dom = new JSDOM(fs.readFileSync('src/aimemory/assets/desktop.html', 'utf8'), {
  runScripts: 'outside-only', url: 'http://127.0.0.1/', pretendToBeVisual: true,
});
const w = dom.window;
w.lucide = { createIcons() {} };
w.fetch = () => new Promise(() => {});
w.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
w.HTMLDialogElement.prototype.close = function () { this.open = false; };
w.eval(fs.readFileSync('src/aimemory/assets/desktop.js', 'utf8'));
const el = id => w.document.getElementById(id);
const base = {
  storage: { provider: 'google-drive', cloud_sync: 'configured', remote: 'AI-Memory' },
  health: { state: 'running', label: 'Collecte en continu' }, watcher: {},
  recent_conversations: [], job: {}, icloud_auth: {},
};
w.render({...base, icloud_auth: {status: 'needs_2fa'}});
assert.equal(el('icloud-credentials').hidden, true);
assert.equal(el('icloud-2fa-label').hidden, false);
w.render({...base, job: {running:true}, icloud_auth: {status:'checking_access', message:'Verification'}});
assert.equal(el('icloud-credentials').hidden, true);
assert.equal(el('icloud-2fa-label').hidden, true);
w.render({...base, storage:{...base.storage, provider:'icloud-online'}, job:{running:true}});
assert.equal(el('cloud-connect-form').hidden, true);
assert.equal(el('icloud-web-approval').hidden, true);
assert.equal(el('cloud-folder-settings').hidden, false);
// A previous job result cannot recreate an authorization prompt after success.
w.render({...base, storage:{...base.storage, provider:'icloud-online'}, job:{result:{status:'needs_web_approval'}}});
assert.equal(el('icloud-web-approval').hidden, true);
assert.equal(el('cloud-connect-form').hidden, true);
el('cloud-switch').click();
assert.equal(el('cloud-connect-form').hidden, false);
assert.equal(el('provider').value, 'icloud-online');
el('cloud-folder').focus();
el('cloud-folder').value = 'Sauvegardes/Conversations';
w.render(base);
assert.equal(el('cloud-folder').value, 'Sauvegardes/Conversations');
w.render({...base, sync_active:false, sync:{status:'waiting_local_cloud', error:'Dossier indisponible'}});
assert.match(el('cloud-detail').textContent, /En attente du dossier cloud local/);
assert.match(el('cloud-detail').textContent, /Dossier indisponible/);
w.render({...base, storage:{...base.storage, provider:'icloud-online'}, sync_active:true,
  sync:{status:'uploading', error:null}, job:{error:true, message:'Ancienne erreur iCloud'}});
assert.equal(el('message').hidden, true);
w.render({...base, watcher:{running:true, last_error:'Too many open files'}, watcher_service:{installed:true},
  health:{state:'error', label:'Collecte en erreur'}});
assert.equal(el('watcher-enable').hidden, false);
assert.equal(el('watcher-enable').textContent, 'Réparer');
w.render({...base, sync_active:true, job:{running:true}});
assert.equal(w.document.querySelector('[data-action="open-folder"]').disabled, false);
assert.equal(el('sync-pause').disabled, false);
assert.equal(el('watcher-enable').disabled, true);
assert.equal(el('menubar-login').disabled, false);
el('provider').value = 'dropbox-online';
el('provider').dispatchEvent(new w.Event('change'));
w.render({...base, icloud_auth:{status:'needs_2fa'}});
assert.equal(el('provider').value, 'dropbox-online');
w.render({...base, sync_paused:true, sync:{status:'paused'}});
assert.match(el('cloud-detail').textContent, /suspendus/);
w.render({...base, sync:{status:'synced', confirmation:'local_folder'}});
assert.match(el('cloud-detail').textContent, /Copie locale/);
w.render({...base, sync_active:true, sync:{status:'downloading', transfers:0, totalTransfers:12, confirmedBefore:166}});
assert.match(el('cloud-detail').textContent, /166 d.*j.* confirm/);
assert.equal(w.sourceName('codex-desktop'), 'Codex Desktop');
assert.equal(w.sourceName('vscode-codex'), 'Codex VS Code');
assert.equal(w.sourceName('codex'), 'Codex');
w.render({...base, menubar_available:true, status_icon_platform:'windows'});
assert.equal(el('status-icon-label').textContent, 'Icône dans la zone de notification');
assert.match(w.document.querySelector('.local-label').textContent, /Sur cet ordinateur/);
w.render({...base, menubar_available:true, status_icon_platform:'mac'});
assert.match(w.document.querySelector('.local-label').textContent, /Sur votre Mac/);

void (async () => {
  w.fetch = async () => { throw new Error('temporary timeout'); };
  await w.refresh();
  await w.refresh();
  assert.notEqual(el('health-label').textContent, 'Interface déconnectée');
  await w.refresh();
  assert.equal(el('health-label').textContent, 'Interface déconnectée');
  w.fetch = async () => ({ok:true, json:async () => ({...base, sync:{status:'synced'}})});
  await w.refresh();
  assert.equal(el('health-label').textContent, 'Collecte en continu');
  assert.equal(el('message').hidden, true);
  dom.window.close();
  console.log('Desktop auth transitions and folder editing passed');
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
