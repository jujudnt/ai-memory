# AI Memory

Mémoire locale et portable pour Codex, Claude et VS Code.
Local, portable memory for Codex, Claude, and VS Code.

[Français](#francais) · [English](#english) · [Télécharger / Download](https://github.com/jujudnt/ai-memory/releases/latest)

---

<a id="francais"></a>

# Français

## Guide utilisateur

### À quoi sert AI Memory ?

AI Memory collecte les conversations IA enregistrées sur votre ordinateur, les rend recherchables et peut les sauvegarder dans votre cloud. Son serveur MCP permet ensuite à Codex, Claude Desktop et VS Code de retrouver ces conversations directement depuis un nouveau chat.

Tout fonctionne localement : aucun serveur AI Memory ni abonnement supplémentaire n'est nécessaire.

Sources actuellement prises en charge :

- sessions Codex locales ;
- Claude Code, sessions de code Claude Desktop et Claude lancé dans VS Code ;
- conversations VS Code enregistrées dans le stockage local de VS Code ;
- projets ou dossiers de travail lorsque cette information existe dans les fichiers locaux.

Les conversations Claude Web, Cowork et Design qui ne sont pas présentes dans les fichiers locaux ne peuvent pas encore être importées. Cursor n'a pas encore d'adaptateur dédié.

### Installation

1. Téléchargez la dernière version depuis [GitHub Releases](https://github.com/jujudnt/ai-memory/releases/latest).
2. Décompressez le fichier correspondant à votre système.
3. Sur macOS, vous pouvez placer `AI Memory.app` où vous le souhaitez. Le dossier **Applications** reste recommandé pour l'organisation, mais il n'est pas obligatoire.
4. Ouvrez AI Memory.

La version macOS est signée avec un certificat Apple Developer ID et notarialisée par Apple.

### 1. Activer la collecte

Dans l'écran principal, cliquez sur **Activer** à côté de **Collecte en continu**.

AI Memory effectue alors un premier import de l'historique déjà présent sur l'ordinateur. Il ne collecte donc pas uniquement les nouvelles conversations. Ensuite, le watcher vérifie régulièrement les changements, même lorsque le tableau de bord est fermé.

Lors de l'activation, AI Memory installe son exécutable de fond dans `~/.ai-memory/bin/ai-memory-cli`. Le watcher ne dépend donc plus de l'emplacement de `AI Memory.app` : l'application peut être déplacée ou supprimée après l'installation sans casser la collecte.

Sur macOS, l'icône AI Memory dans la barre des menus indique son état :

- icône pleine : collecte active ;
- icône vide : démarrage ou collecte inactive ;
- triangle d'avertissement : erreur de collecte, erreur cloud ou dernier scan trop ancien.

**Quitter l'interface** ferme le tableau de bord et son icône, mais n'arrête pas le watcher installé.

### 2. Connecter une sauvegarde cloud

Cliquez sur **Connecter**, puis choisissez une destination :

- Google Drive ;
- Dropbox ;
- OneDrive ;
- iCloud Drive en ligne ;
- iCloud Drive, Dropbox ou OneDrive déjà synchronisé sur le Mac ;
- un dossier local ou synchronisé personnalisé.

Après la connexion, ouvrez **Réglages > Dossier de sauvegarde** pour voir le chemin exact ou choisir un autre dossier dans le drive.

La première sauvegarde peut être longue. L'interface affiche la progression, la dernière activité, les erreurs éventuelles et l'espace local récupéré. Les transferts interrompus reprennent en ignorant les objets déjà confirmés dans le cloud.

**Synchroniser signifie envoyer ET recevoir.** « Envoi » sauvegarde les données de ce Mac ; « Réception » récupère celles de la destination, notamment de l'autre ordinateur. Le compteur indique des objets (versions et originaux), pas un nombre de conversations. Le bouton pause suspend les transferts ; la flèche de synchronisation permet de reprendre.

#### Changer de compte, de drive ou de dossier

Utilisez **Changer de destination**. AI Memory récupère d'abord tous les originaux et toutes les versions de l'ancienne destination, y compris ceux déjà nettoyés du Mac, puis les copie vers la nouvelle. La migration reste enregistrée jusqu'à sa fin. L'ancien dossier distant n'est pas supprimé. Prévoyez assez de place locale pour cette étape ; si l'ancien Drive est inaccessible, le changement est bloqué sans annoncer une migration réussie.

Pour renouveler une autorisation expirée **sans changer de compte**, utilisez **Reconnecter ce compte** : cette action ne dépend pas d'un téléchargement préalable depuis la session expirée et conserve le dossier choisi. Pour utiliser un autre compte, passez par **Changer de destination**. OneDrive peut demander de choisir le Drive après l'autorisation Microsoft.

Si un drive est plein, libérez de l'espace ou changez de destination. AI Memory conserve les données qui ne sont pas encore confirmées dans le cloud.

#### Cas particulier : iCloud

- **iCloud Drive du Mac** utilise le compte déjà connecté à macOS. Les deux Mac doivent utiliser le même compte iCloud et le même dossier de sauvegarde.
- **iCloud Drive en ligne** utilise une connexion distincte. Entrez d'abord l'identifiant et le mot de passe Apple, validez la demande sur l'appareil Apple, puis saisissez le code dans le champ 2FA qui apparaît et cliquez sur **Confirmer le code iCloud**.
- Avec la Protection avancée des données, activez **Accès aux données iCloud sur le Web** dans les réglages du compte Apple et acceptez l'éventuelle demande supplémentaire.
- Si Apple demande d'accepter de nouvelles conditions, connectez-vous une fois sur [icloud.com](https://www.icloud.com/), acceptez-les, puis relancez la connexion dans AI Memory.
- Si un fichier iCloud local reste indisponible, ouvrez `iCloud Drive/AI-Memory` dans Finder et choisissez **Télécharger** ou **Télécharger maintenant**.

### 3. Installer le MCP

Le MCP permet à vos assistants IA d'interroger AI Memory. Il ne s'agit pas d'une nouvelle application de chat.

1. Ouvrez AI Memory.
2. Cliquez sur l'icône **Réglages** en haut à droite.
3. Dans **MCP pour Codex et Claude**, cliquez sur **Activer**.
4. Fermez complètement puis relancez Codex, Claude Desktop et VS Code.

Le bouton installe automatiquement AI Memory dans tous les clients détectés sur cet ordinateur :

| Client | Configuration écrite |
| --- | --- |
| Codex | `~/.codex/config.toml` |
| Claude Desktop | `~/Library/Application Support/Claude/claude_desktop_config.json` sur macOS |
| Claude Code, terminal et extension VS Code | `~/.claude.json`, configuration utilisateur |
| VS Code | fichier utilisateur `mcp.json` |

Ces configurations pointent vers la copie autonome `~/.ai-memory/bin/ai-memory-cli`, jamais vers l'emplacement de l'application. Vous pouvez donc déplacer AI Memory après l'activation. Sous Windows, cette copie est placée dans `%LOCALAPPDATA%\AI Memory\bin`. Les clients absents sont ignorés ; avoir uniquement Claude ou uniquement Codex est suffisant. `CODEX_HOME` et `CLAUDE_CONFIG_DIR` sont respectés lorsqu'ils sont définis dans l'environnement de l'application.

L'installation dans un client n'installe pas automatiquement le MCP dans les autres : le bouton AI Memory configure en une fois tous ceux qui sont présents. Si vous installez plus tard un nouveau client, cliquez sur **Réparer** (ou **Activer** si le MCP n'est pas encore configuré). Après une mise à jour depuis une version antérieure à `0.4.18`, utilisez également ce bouton pour migrer l'ancienne configuration et le composant de sauvegarde cloud.

#### Vérifier que le MCP est connecté

- **Codex** : tapez `/mcp` et vérifiez que `ai-memory` est connecté.
- **Claude Desktop** : ouvrez la liste des outils ou connecteurs et vérifiez que les outils AI Memory apparaissent.
- **Claude Code** : utilisez `/mcp`. La configuration utilisateur est partagée avec son extension VS Code ; le MCP natif de VS Code est une configuration distincte. [Documentation Claude Code](https://code.claude.com/docs/en/mcp).
- **VS Code** : ouvrez la palette avec `Cmd + Shift + P` ou `Ctrl + Shift + P`, lancez **MCP: List Servers**, puis vérifiez `ai-memory`.

#### Utiliser le MCP

Parlez normalement à Codex, Claude ou votre agent VS Code. Pour rendre l'intention explicite, mentionnez AI Memory :

```text
Utilise AI Memory pour retrouver la conversation où nous avons configuré iCloud.
```

```text
Cherche dans AI Memory mes derniers échanges concernant le projet Sabai et résume les décisions.
```

```text
Vérifie avec AI Memory quand la dernière synchronisation cloud a réussi.
```

Le MCP expose les outils suivants :

- `search_conversations` : recherche plein texte ;
- `get_conversation` : lecture paginée d'une conversation (`offset`, `limit`, `next_offset`) ;
- `list_conversations` : liste filtrée par source ou projet ;
- `get_project_history` : historique d'un projet ;
- `list_projects` : noms, chemins et identifiants pour distinguer les projets homonymes ;
- `get_sync_status` : état du watcher et du cloud ;
- `sync_now` : demande de synchronisation au watcher, sans bloquer le client pendant le transfert. Le watcher doit être actif.

La recherche accepte des dates ISO, un nom ou identifiant de projet, et les familles `codex`, `claude` ou `vscode`, incluant leurs variantes. Deux dossiers de même nom ne sont pas fusionnés uniquement à cause de leur nom. Les projets créés indépendamment sur plusieurs machines peuvent garder des identifiants distincts.

### Utiliser plusieurs ordinateurs

Le libellé **Codex Desktop** ou **Codex VS Code** repose sur l'origine explicite enregistrée dans la session. Le champ technique `source=vscode` est partagé par les deux applications et ne suffit pas à les distinguer. Sans origine explicite reconnue, AI Memory affiche simplement **Codex**. Les anciennes archives sont reconnues sans réenvoi pour ce seul changement de libellé.

1. Installez AI Memory sur chaque ordinateur.
2. Connectez la même destination et choisissez le même dossier distant.
3. Attendez la fin de la synchronisation.
4. Activez le MCP sur chaque ordinateur.

Chaque machine reconstruit son propre index de recherche à partir des archives téléchargées. AI Memory ne réinjecte pas les conversations dans la barre latérale native de Codex ou Claude ; elles restent consultables via AI Memory et le MCP.

### Stockage et nettoyage automatique

AI Memory affiche séparément :

- **Conversations et archives** : total des conversations consultables, sources originales et versions archivées ; le détail est affiché séparément ;
- **Cache et transferts en attente** : fichiers temporaires, y compris les réceptions interrompues ;
- **Index de recherche** : base SQLite utilisée pour les recherches rapides et hors ligne ;
- **Total sur cet ordinateur** : ensemble des fichiers AI Memory locaux, y compris fichiers temporaires et état de synchronisation.

Après une sauvegarde cloud vérifiée, AI Memory supprime automatiquement du Mac les originaux compressés et les anciennes révisions lourdes qui sont confirmés dans le cloud. Les conversations courantes et l'index restent disponibles localement afin que le MCP soit rapide et fonctionne hors ligne.

Pour **iCloud/OneDrive/Dropbox du Mac**, une copie locale ne prouve pas l'envoi aux serveurs du fournisseur : le statut l'indique et AI Memory conserve ses originaux. Les ajouts aux sources utilisent des deltas, avec une base complète au maximum toutes les 32 dépendances. L'index peut être compacté automatiquement hors import/transfert si l'espace disponible le permet. Le nettoyage des anciens ZIP vise le cache AI Memory, pas les téléchargements personnels du navigateur.

Il n'y a pas encore de limite automatique de taille de l'historique cloud : les versions réellement distinctes sont conservées. Une migration peut donc demander de l'espace ; AI Memory s'arrête avec une erreur explicite si la réserve disque est insuffisante plutôt que supprimer une copie non confirmée.

Les transferts sont adressés par le hash du contenu : relancer une synchronisation ne crée pas une seconde copie du même objet. AI Memory fusionne également les snapshots dont le contenu conversationnel est identique, même si Codex ou Claude les a signalés sous un autre client, puis retire ces variantes redondantes du cloud.

### Confidentialité et sécurité

- Les conversations et l'index restent sur l'ordinateur, sauf les archives envoyées vers la destination choisie.
- La base SQLite et les identifiants cloud ne sont pas téléversés.
- Les autorisations cloud sont stockées localement dans le dossier `credentials`, accessible uniquement à l'utilisateur sur macOS et Linux.
- **Les archives cloud ne sont pas chiffrées de bout en bout dans cette version.** La sécurité au repos dépend du fournisseur choisi.
- **Déconnecter** supprime l'autorisation locale, mais ne supprime pas les archives distantes. Révoquez aussi l'accès rclone dans le compte du fournisseur si nécessaire.

### Dépannage rapide

| Problème | Solution |
| --- | --- |
| Le MCP n'apparaît pas | Cliquez sur **Réparer** (ou **Activer** lors de la première configuration), puis quittez complètement et relancez le client. |
| La collecte est en erreur | Utilisez **Réparer** après la fin de l'import/transfert, ou suspendez les transferts avant la réparation. |
| Les anciennes conversations manquent | Laissez finir le premier import et vérifiez l'état du watcher dans l'icône de menu. |
| Le cloud est plein | Libérez de l'espace ou utilisez **Changer de destination**. |
| iCloud affiche `Resource deadlock avoided` | Ouvrez le dossier dans Finder, forcez son téléchargement et laissez le watcher réessayer. |
| La connexion Google cesse de fonctionner | Configurez votre propre client OAuth Desktop selon la [documentation rclone](https://rclone.org/drive/#making-your-own-client-id). |

## Guide développeur

### Validation 0.4.21

Les corrections présentes sur `main` ne sont pas une preuve de publication dans GitHub Releases. L'audit initial et le suivi des corrections sont dans [le rapport d'audit](docs/audit-2026-09-21.md). Les connexions cloud des tests utilisent des doublures et des dossiers temporaires, pas des comptes réels.

Le build macOS doit transmettre `--codesign-identity` **aux deux appels PyInstaller**, avant l'empaquetage des bibliothèques. Une signature extérieure seule ne suffit pas pour le CLI `--onefile`. Avant publication, lancer `python scripts/smoke_packaged.py "dist/AI Memory.app/Contents/MacOS/ai-memory-cli"` : démarrage du binaire signé, scan isolé, négociation MCP et recherche. Aucun changement de l'installation utilisateur n'est effectué par ce test.

### Architecture

AI Memory suit un modèle local-first :

1. les adaptateurs découvrent les sessions Codex, Claude et VS Code ;
2. les messages sont normalisés dans un schéma commun ;
3. les originaux compressés et les révisions immuables sont archivés ;
4. SQLite FTS5 indexe la dernière version de chaque conversation ;
5. le watcher importe et synchronise périodiquement ;
6. le serveur MCP stdio expose les fonctions de recherche aux clients locaux.

Aucun backend hébergé, service d'embeddings payant ou base vectorielle propriétaire n'est requis.

### Installation depuis les sources

Prérequis : Python 3.11 ou plus récent et Node.js.

```bash
git clone https://github.com/jujudnt/ai-memory.git
cd ai-memory
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev,desktop]"
npm ci
python scripts/fetch_rclone.py
```

Lancer les vérifications puis l'interface :

```bash
aimemory doctor
aimemory import-all
aimemory desktop
```

Sans installer le script CLI :

```bash
PYTHONPATH=src python -m aimemory.cli doctor
```

### Commandes utiles

```bash
aimemory import-all                  # Importe Codex, Claude et VS Code
aimemory import-all --force          # Force une nouvelle analyse complète
aimemory search "requête"           # Recherche locale
aimemory get CONVERSATION_ID         # Affiche une conversation normalisée
aimemory audit                       # Vérifie originaux et archives normalisées
aimemory sync                        # Lance la synchronisation configurée
aimemory watch --interval 10         # Lance le watcher au premier plan
aimemory watch-status                # Affiche le dernier état du watcher
aimemory install-watcher             # Installe le watcher au démarrage
aimemory install-mcp                 # Configure tous les clients MCP détectés
aimemory mcp-config                  # Affiche une configuration Codex manuelle
aimemory mcp-server                  # Lance le serveur MCP stdio
```

Le répertoire de données par défaut est `~/.ai-memory`. Pour le remplacer :

```bash
export AI_MEMORY_HOME=/chemin/vers/ai-memory
```

### Arborescence des données

| Chemin | Contenu |
| --- | --- |
| `archive/sources/<source>/sessions` | Version normalisée courante des conversations |
| `archive/raw` | Originaux compressés en attente de confirmation cloud |
| `archive/snapshots` | Révisions normalisées immuables en attente de confirmation |
| `db/memory.sqlite` | Métadonnées et index plein texte FTS5 |
| `cache/incoming` | Zone temporaire de vérification des téléchargements |
| `credentials` | Autorisations cloud locales |
| `bin/ai-memory-cli` | Runtime autonome utilisé par le watcher et le MCP |
| `state/watcher-status.json` | Santé et dernier résultat du watcher |
| `state/audit.json` | Dernier rapport d'intégrité |

Les anciennes versions et les forks restent séparés. Il n'existe pas encore de fusion sémantique automatique des conflits.

### MCP en développement

Pour écrire automatiquement la configuration dans les clients disponibles :

```bash
aimemory install-mcp
```

Pour Codex uniquement :

```bash
codex mcp add ai-memory -- aimemory-mcp
```

La configuration manuelle et les formats JSON/TOML sont détaillés dans [docs/mcp.md](docs/mcp.md).

### Tests

```bash
pytest -q
npm test
```

### Build et publication

Les releases sont construites par [`.github/workflows/release.yml`](.github/workflows/release.yml) lors de l'envoi d'un tag `v*`. Le workflow exécute les tests, construit macOS et Windows, signe l'application macOS avec Developer ID, la soumet à la notarisation Apple, agrafe le ticket puis publie les ZIP.

Les secrets GitHub nécessaires à la signature sont décrits dans [docs/release.md](docs/release.md).

### Périmètre actuel

Disponible : import Codex/Claude/VS Code, archives versionnées, recherche FTS5, watcher, synchronisation cloud et dossiers locaux, nettoyage après vérification, interface desktop, barre de menus macOS et MCP local.

Non disponible pour le moment : recherche vectorielle, collecte événementielle à la place du polling, adaptateur Cursor dédié, import Claude Web/Cowork/Design sans export local stable, stockage GitHub privé et chiffrement de bout en bout optionnel. Consultez [docs/roadmap.md](docs/roadmap.md).

## Licence

[MIT](LICENSE)

---

<a id="english"></a>

# English

## User guide

### What does AI Memory do?

AI Memory collects AI conversations stored on your computer, makes them searchable, and can back them up to your cloud storage. Its MCP server then lets Codex, Claude Desktop, and VS Code retrieve those conversations directly from a new chat.

Everything runs locally. No AI Memory server or additional subscription is required.

Currently supported sources:

- local Codex sessions;
- Claude Code, Claude Desktop coding sessions, and Claude launched from VS Code;
- VS Code conversations stored in VS Code's local user data;
- project or working-directory metadata when it is present in local files.

Claude Web, Cowork, and Design conversations that are not available in local files cannot be imported yet. Cursor does not have a dedicated adapter yet.

### Installation

1. Download the latest version from [GitHub Releases](https://github.com/jujudnt/ai-memory/releases/latest).
2. Extract the archive for your operating system.
3. On macOS, `AI Memory.app` can be kept anywhere. **Applications** is still recommended for organization, but it is not required.
4. Open AI Memory.

The macOS release is signed with an Apple Developer ID certificate and notarized by Apple.

### 1. Enable collection

On the main screen, click **Activer** next to **Collecte en continu**.

AI Memory first imports the history already present on the computer. It does not collect only new conversations. The watcher then checks for changes regularly, even after the dashboard is closed.

When collection is enabled, AI Memory installs its background executable at `~/.ai-memory/bin/ai-memory-cli`. The watcher no longer depends on the location of `AI Memory.app`, so moving or deleting the app after installation does not break collection.

On macOS, the AI Memory menu-bar icon shows its state:

- filled icon: collection is healthy;
- outline icon: starting or inactive;
- warning triangle: collection error, cloud error, or stale last scan.

**Quitter l'interface** closes the dashboard and icon but does not stop the installed watcher.

### 2. Connect cloud backup

Click **Connecter**, then choose a destination:

- Google Drive;
- Dropbox;
- OneDrive;
- online iCloud Drive;
- iCloud Drive, Dropbox, or OneDrive already synchronized on the Mac;
- a custom local or synchronized folder.

After connecting, open **Réglages > Dossier de sauvegarde** to see the exact path or choose another folder inside the drive.

The first backup may take a while. The UI shows progress, recent activity, errors, and reclaimed local space. Interrupted transfers resume by skipping objects already confirmed in the cloud.

**Synchronization sends AND receives.** Upload saves this computer's data; download retrieves data from the destination, including another computer's archives. Counters show objects (revisions and originals), not conversations. Pause suspends transfers; the synchronization arrow resumes them.

#### Change account, drive, or folder

Use **Changer de destination**. AI Memory retrieves every original and revision from the previous destination, including objects already pruned from this Mac, then copies them to the new destination. Migration state persists until completion. The previous remote folder is not deleted. Allow enough local disk space for staging; an inaccessible source blocks migration instead of reporting success.

To renew expired authorization **without changing accounts**, use **Reconnecter ce compte**. This does not require downloading from the expired session first and preserves the selected folder. To use a different account, use **Changer de destination**. OneDrive may ask you to select a drive after Microsoft authorization.

If a drive is full, free some space or change destination. AI Memory retains data that has not yet been confirmed in the cloud.

#### iCloud notes

- **iCloud Drive du Mac** uses the account already signed in to macOS. Both Macs must use the same iCloud account and backup folder.
- **Online iCloud Drive** uses a separate login. Enter the Apple ID and password first, approve the request on the Apple device, enter the code in the 2FA field that appears, then click **Confirmer le code iCloud**.
- With Advanced Data Protection, enable **Access iCloud Data on the Web** in Apple Account settings and approve any additional prompt.
- If Apple requires updated terms to be accepted, sign in once at [icloud.com](https://www.icloud.com/), accept them, then retry in AI Memory.
- If a local iCloud file remains unavailable, open `iCloud Drive/AI-Memory` in Finder and select **Download** or **Download Now**.

### 3. Install MCP

MCP lets your AI clients query AI Memory. It is not a separate chat application.

1. Open AI Memory.
2. Click the **Settings** icon in the top-right corner.
3. Under **MCP pour Codex et Claude**, click **Activer**.
4. Fully quit and restart Codex, Claude Desktop, and VS Code.

The button configures every supported client detected on that computer:

| Client | Configuration written by AI Memory |
| --- | --- |
| Codex | `~/.codex/config.toml` |
| Claude Desktop | `~/Library/Application Support/Claude/claude_desktop_config.json` on macOS |
| Claude Code, terminal and VS Code extension | user-scoped `~/.claude.json` |
| VS Code | user-level `mcp.json` |

These configurations point to the standalone copy at `~/.ai-memory/bin/ai-memory-cli`, never to the application location. You can therefore move AI Memory after enabling it. On Windows, the copy is installed under `%LOCALAPPDATA%\AI Memory\bin`. Missing clients are skipped; using only Claude or only Codex is supported. `CODEX_HOME` and `CLAUDE_CONFIG_DIR` are respected when present in the application's environment.

Installing MCP in one client does not install it in the others by itself. The AI Memory button handles all clients currently installed. If you install another client later, click **Réparer** (Repair), or **Activer** (Enable) if MCP has not been configured yet. After upgrading from a version older than `0.4.18`, use this button as well to migrate the previous configuration and cloud backup component.

#### Verify the MCP connection

- **Codex**: enter `/mcp` and confirm that `ai-memory` is connected.
- **Claude Desktop**: open the tools or connectors list and check for AI Memory tools.
- **Claude Code**: use `/mcp`. Its user configuration is shared with its VS Code extension; VS Code's native MCP configuration is separate. [Claude Code documentation](https://code.claude.com/docs/en/mcp).
- **VS Code**: open the command palette with `Cmd + Shift + P` or `Ctrl + Shift + P`, run **MCP: List Servers**, and check for `ai-memory`.

#### Use MCP

Talk normally to Codex, Claude, or your VS Code agent. Mention AI Memory when you want to make the tool choice explicit:

```text
Use AI Memory to find the conversation where we configured iCloud.
```

```text
Search AI Memory for my latest discussions about the Sabai project and summarize the decisions.
```

```text
Use AI Memory to check when the last cloud synchronization succeeded.
```

The MCP server exposes:

- `search_conversations`: full-text search;
- `get_conversation`: paginated retrieval (`offset`, `limit`, `next_offset`);
- `list_conversations`: list conversations by source or project;
- `get_project_history`: retrieve project history;
- `list_projects`: discover project names, paths and IDs to distinguish identical names;
- `get_sync_status`: inspect watcher and cloud state;
- `sync_now`: queue a request for the watcher without blocking the client during transfers. The watcher must be running.

Search supports ISO dates, a project name or ID, and the `codex`, `claude` and `vscode` source families including their variants. Unrelated folders are not merged solely because their names match. Projects created independently on different computers may retain distinct IDs.

### Use multiple computers

The **Codex Desktop** or **Codex VS Code** label uses the explicit origin recorded in the session. Both applications can write the technical field `source=vscode`, so that field alone cannot distinguish them. Without a recognized explicit origin, AI Memory displays **Codex**. Existing archives are recognized without re-uploading them solely to change their label.

1. Install AI Memory on each computer.
2. Connect the same destination and select the same remote folder.
3. Wait for synchronization to complete.
4. Enable MCP on each computer.

Each machine builds its own search index from downloaded archives. AI Memory does not restore conversations into the native Codex or Claude sidebar; they remain available through AI Memory and MCP.

### Storage and automatic cleanup

AI Memory reports these values separately:

- **Conversations and archives**: total of readable conversations, original sources and archived revisions, with separate detailed sizes;
- **Cache and pending transfers**: temporary files, including interrupted downloads;
- **Search index**: the SQLite database used for fast and offline search;
- **Total on this computer**: all local AI Memory files, including temporary files and synchronization state.

After a verified cloud backup, AI Memory automatically removes compressed originals and large historical revisions from the Mac when they are confirmed in the cloud. Current conversations and the search index remain local so MCP stays fast and works offline.

For **iCloud/OneDrive/Dropbox on the Mac**, a local copy does not prove server upload: the UI states this and AI Memory retains its originals. Source appends use deltas, with a full base at most every 32 dependencies. SQLite can be compacted automatically outside imports/transfers when sufficient disk space is available. Old ZIP cleanup covers AI Memory's cache, not personal browser downloads.

There is not yet an automatic cloud history size limit: genuinely distinct revisions are retained. Migration may require substantial local space; AI Memory stops with an explicit error if its free-space reserve is insufficient rather than deleting an unconfirmed copy.

Transfers are content-addressed: restarting synchronization does not create another copy of the same object. AI Memory also merges snapshots with identical conversation content even when Codex or Claude reported them under another client, then removes those redundant variants from the cloud.

### Privacy and security

- Conversations and the search index remain on the computer, except for archives sent to the selected destination.
- The SQLite database and cloud credentials are not uploaded.
- Cloud authorization is stored locally in the `credentials` directory, restricted to the local user on macOS and Linux.
- **Cloud archives are not end-to-end encrypted in this release.** At-rest protection depends on the selected provider.
- **Disconnect** removes the local authorization but does not delete remote archives. Revoke rclone access in the provider account as well when required.

### Quick troubleshooting

| Problem | Solution |
| --- | --- |
| MCP does not appear | Click **Réparer** (Repair), or **Activer** (Enable) for the initial setup, then fully quit and restart the client. |
| Collection reports an error | Use **Réparer** once imports/transfers finish, or pause transfers before repairing. |
| Older conversations are missing | Let the first import complete and inspect the watcher state from the menu-bar icon. |
| Cloud storage is full | Free space or use **Changer de destination**. |
| iCloud reports `Resource deadlock avoided` | Open the folder in Finder, force its download, and let the watcher retry. |
| Google authentication stops working | Configure your own Desktop OAuth client using the [rclone documentation](https://rclone.org/drive/#making-your-own-client-id). |

## Developer guide

### 0.4.21 validation

Fixes on `main` do not imply that a GitHub Release has been published. The initial audit and remediation record are in [the audit report](docs/audit-2026-09-21.md). Cloud tests use doubles and temporary directories, not real accounts.

macOS builds must pass `--codesign-identity` **to both PyInstaller invocations**, before embedding libraries. Signing only the outside of a `--onefile` executable is insufficient. Before publication, run `python scripts/smoke_packaged.py "dist/AI Memory.app/Contents/MacOS/ai-memory-cli"` to exercise the signed executable, an isolated watcher scan, MCP initialization and search. This test does not modify the user's installation.

### Architecture

AI Memory follows a local-first pipeline:

1. adapters discover Codex, Claude, and VS Code sessions;
2. messages are normalized into a source-independent schema;
3. compressed originals and immutable revisions are archived;
4. SQLite FTS5 indexes the latest revision of each conversation;
5. the watcher imports and synchronizes periodically;
6. the stdio MCP server exposes local search to supported clients.

No hosted backend, paid embedding service, or proprietary vector database is required.

### Install from source

Requirements: Python 3.11 or newer and Node.js.

```bash
git clone https://github.com/jujudnt/ai-memory.git
cd ai-memory
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev,desktop]"
npm ci
python scripts/fetch_rclone.py
```

Run the initial checks and desktop UI:

```bash
aimemory doctor
aimemory import-all
aimemory desktop
```

Without installing the CLI entry point:

```bash
PYTHONPATH=src python -m aimemory.cli doctor
```

### CLI reference

```bash
aimemory import-all                  # Import Codex, Claude, and VS Code
aimemory import-all --force          # Force a complete rescan
aimemory search "query"              # Search locally
aimemory get CONVERSATION_ID         # Print a normalized conversation
aimemory audit                       # Verify originals and normalized archives
aimemory sync                        # Run configured synchronization
aimemory watch --interval 10         # Run the watcher in the foreground
aimemory watch-status                # Print the latest watcher state
aimemory install-watcher             # Install the login watcher
aimemory install-mcp                 # Configure every detected MCP client
aimemory mcp-config                  # Print manual Codex configuration
aimemory mcp-server                  # Run the stdio MCP server
```

The default data directory is `~/.ai-memory`. Override it with:

```bash
export AI_MEMORY_HOME=/path/to/ai-memory
```

### Data layout

| Path | Contents |
| --- | --- |
| `archive/sources/<source>/sessions` | Current normalized conversation revisions |
| `archive/raw` | Compressed originals awaiting cloud confirmation |
| `archive/snapshots` | Immutable normalized revisions awaiting confirmation |
| `db/memory.sqlite` | Metadata and FTS5 full-text index |
| `cache/incoming` | Temporary download verification area |
| `credentials` | Local cloud authorization |
| `bin/ai-memory-cli` | Standalone runtime used by the watcher and MCP |
| `state/watcher-status.json` | Watcher health and latest result |
| `state/audit.json` | Latest integrity report |

Older revisions and forks remain separate. Automatic semantic conflict merging is not implemented yet.

### MCP during development

Write configuration for every available client:

```bash
aimemory install-mcp
```

For Codex only:

```bash
codex mcp add ai-memory -- aimemory-mcp
```

Manual JSON and TOML formats are documented in [docs/mcp.md](docs/mcp.md).

### Tests

```bash
pytest -q
npm test
```

### Build and release

Releases are built by [`.github/workflows/release.yml`](.github/workflows/release.yml) when a `v*` tag is pushed. The workflow runs tests, builds macOS and Windows, signs the macOS application with Developer ID, submits it for Apple notarization, staples the ticket, and publishes both ZIP files.

Required GitHub signing secrets are documented in [docs/release.md](docs/release.md).

### Current scope

Available today: Codex/Claude/VS Code import, revision archives, FTS5 search, watcher, cloud and local-folder synchronization, verified cleanup, desktop UI, macOS menu-bar status, and local MCP.

Not currently available: vector search, filesystem events instead of polling, a dedicated Cursor adapter, Claude Web/Cowork/Design import without a stable local export, private GitHub storage, and optional end-to-end encryption. See [docs/roadmap.md](docs/roadmap.md).

## License

[MIT](LICENSE)
