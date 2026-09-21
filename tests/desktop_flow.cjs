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
dom.window.close();
console.log('Desktop auth transitions and folder editing passed');
