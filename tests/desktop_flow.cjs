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
assert.equal(w.document.documentElement.dataset.platform, 'mac');

// Progressive disclosure: one view at a time, conversations first.
const today = new Date().toISOString();
w.render({...base, conversation_count: 30, archive_count: 30, project_count: 2,
  storage: {...base.storage, provider_label: 'iCloud Drive', total_bytes: 3000, archive_bytes: 2000,
    normalized_bytes: 1200, raw_bytes: 300, revisions_bytes: 500, cache_bytes: 100, database_bytes: 400},
  recent_conversations: [
    {source: 'codex-desktop', latest_user_message: 'Premier sujet', project_name: 'sabai', updated_at: today},
    {source: 'claude-desktop', title: '{"a": 1}; {b}; <c>', updated_at: '2026-01-02T10:00:00Z'},
  ]});
assert.equal(el('view-conv').hidden, false);
assert.equal(el('view-proj').hidden, true);
assert.match(el('overview-subtitle').textContent, /30 conversations.*Tous les appareils.*iCloud Drive/);
assert.deepEqual([...w.document.querySelectorAll('#conversations .grouplabel')].map(n => n.textContent),
  ["Aujourd'hui", 'Plus tôt']);
assert.equal(w.document.querySelector('.conversation-title.untitled').textContent, 'Conversation sans titre');
assert.match(el('history-count').textContent, /2 plus r.centes sur 30/);
w.document.querySelector('.navi[data-view="store"]').click();
assert.equal(el('view-store').hidden, false);
assert.equal(el('view-conv').hidden, true);
assert.equal(el('search-box').hidden, true);
assert.equal(el('view-title').textContent, 'Stockage');
assert.equal(w.document.querySelector('.navi[data-view="store"]').getAttribute('aria-current'), 'page');
assert.equal(w.document.querySelector('.navi[data-view="conv"]').hasAttribute('aria-current'), false);
assert.equal(w.document.querySelectorAll('#store-list .store-row').length, 6);
assert.equal(w.document.querySelector('#store-list .s-val').textContent, '1,2 Ko');
w.document.querySelector('.navi[data-view="conv"]').click();
assert.equal(el('view-conv').hidden, false);
assert.equal(el('search-box').hidden, false);
w.render({...base, recent_conversations: [
  {source: 'codex-desktop', latest_user_message: 'Premier sujet', project_name: 'sabai', name: 'Build AI Memory V1', updated_at: today},
  {source: 'claude-desktop', latest_user_message: 'Même texte', name: 'Même texte', updated_at: today},
]});
assert.deepEqual([...w.document.querySelectorAll('.conversation-title')].map(n => n.textContent), ['Build AI Memory V1', 'Même texte']);

// The folder dialog button appears only where a local copy can be browsed.
w.render({...base, folder_picker: true, backup_folder_picker: false});
assert.equal(el('cloud-folder-pick').hidden, true);
assert.equal(el('folder-pick').hidden, false);
w.render({...base, folder_picker: true, backup_folder_picker: true});
assert.equal(el('cloud-folder-pick').hidden, false);

// Theme choice applies immediately and survives the next status poll.
w.fetch = () => new Promise(() => {});
w.document.querySelector('[data-theme-choice="dark"]').click();
assert.equal(w.document.documentElement.dataset.theme, 'dark');
w.render({...base, theme: 'light'});
assert.equal(w.document.documentElement.dataset.theme, 'dark');

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
  w.fetch = async (url) => ({ok: true, json: async () => ({path: 'Sauvegardes/AI'})});
  el('cloud-folder-pick').click();
  await new Promise(resolve => setTimeout(resolve, 0));
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(el('cloud-folder').value, 'Sauvegardes/AI');
  const cloudRow = {id:'cloud-1', source:'codex-desktop', name:'Sabai renommé', project_name:'Client Sabai', device_name:'MacBook Air', updated_at:today};
  const requests = [];
  w.fetch = async (url) => {
    requests.push(String(url));
    const params = new URL(String(url), 'http://127.0.0.1').searchParams;
    return {ok:true, json:async () => String(url).startsWith('/api/conversation?')
      ? {conversation:{messages:[{role:'user', content:'<script>pas du HTML</script>', timestamp:today}], next_offset:null}}
      : {conversations:params.get('offset') === '50' ? [{...cloudRow,id:'cloud-2'}] : [cloudRow],
         has_more:params.get('offset') !== '50', next_offset:params.get('offset') === '50' ? 51 : 50}};
  };
  el('device-filter').value = 'other';
  el('source-filter').value = 'codex';
  await w.loadConversations();
  assert.match(requests.at(-1), /device=other/);
  assert.match(requests.at(-1), /source=codex/);
  assert.equal(el('conversations-more').hidden, false);
  assert.match(el('conversations').textContent, /Sabai renommé.*Client Sabai.*MacBook Air/);
  await w.loadConversations(true);
  assert.match(requests.at(-1), /offset=50/);
  assert.equal(w.document.querySelectorAll('#conversations .conversation').length, 2);
  assert.equal(el('conversations-more').hidden, true);
  await w.openConversation(cloudRow);
  assert.equal(el('conversation-detail').open, true);
  assert.match(el('conversation-messages').textContent, /<script>pas du HTML<\/script>/);
  assert.equal(el('conversation-messages').querySelector('script'), null);
  dom.window.close();
  console.log('Desktop auth, folder editing, cloud library pagination, device filters and reading passed');
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
