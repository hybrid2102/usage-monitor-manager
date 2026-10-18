# Guida utente — Usage Monitor Manager

Questa guida spiega come installare e usare `UsageMonitorManager.exe` (comando
`umm` per la riga di comando), il manager che sostituisce
`usage-monitor-updater` e `usage-monitor-config-editor` per le istanze
`usage-monitor-for-claude/copilot/codex` (e, in generale, qualsiasi exe
portabile aggiornato da GitHub Releases). Riferimenti: [PLAN.md](../PLAN.md)
per il design, [STATUS.md](../STATUS.md) per lo stato di avanzamento,
[docs/status-file.md](status-file.md) per lo schema di `status.json`.

## 1. Installazione

Non esiste un installer. Il manager è un unico eseguibile portabile,
`UsageMonitorManager.exe`, generato con PyInstaller (come i monitor della
famiglia). Basta copiarlo dove si preferisce — tipicamente nella stessa
cartella `Tools\AI` dove vivono già gli exe di Claude/Copilot/Codex — e
avviarlo con un doppio clic. Nessuna registrazione nel registro di sistema,
nessuna voce nel pannello "Programmi e funzionalità": rimuovere il manager
significa cancellare il file (e, se creata, la cartella
`%LOCALAPPDATA%\UsageMonitorManager\`).

Il manager è **singleton**: un secondo avvio mentre uno è già in esecuzione
non apre una seconda icona in tray, si limita a segnalarlo (mutex
`UsageMonitorManager_SingleInstance`).

## 2. Primo avvio e wizard di import legacy

Al primissimo avvio (nessun `manager-config.json` preesistente) il manager
propone di importare tutto quello che gli strumenti precedenti avevano già
configurato, **senza modificare né cancellare nulla alla fonte**:

- gli shortcut legacy `<exe> - *.lnk` accanto agli exe noti (la convenzione
  usata dal vecchio `usage-monitor-updater` per le istanze aggiuntive, es.
  `UsageMonitorForClaude.exe - valeria.lnk`);
- le voci `HKCU\...\Run\UsageMonitorFor*` scritte dai monitor stessi;
- `profiles.json` del vecchio config-editor
  (`%LOCALAPPDATA%\UsageMonitorConfigEditor\profiles.json`), da cui arrivano
  le etichette suggerite (es. "Claude Valeria" per `~/.claude-valeria`).

L'importazione è **una tantum**: da riga di comando si può rivedere prima con

```
umm import-legacy --dry-run
```

che stampa cosa verrebbe importato senza scrivere nulla, ad esempio:

```
umm import-legacy --dry-run: nessuna modifica verra' scritta
  [profiles] Claude: C:\Users\Marco\.claude  (profiles.json (Claude))
  [profiles] Claude Valeria: C:\Users\Marco\.claude-valeria  (profiles.json (Claude Valeria))
  [profiles] Copilot: C:\Users\Marco\.copilot  (profiles.json (Copilot))
  [profiles] Codex: C:\Users\Marco\.codex  (profiles.json (Codex))
```

Nella finestra, la pagina **Manager** ha una sezione "Importazione
configurazione precedente" che rifà la stessa cosa con un clic, mostrando
l'elenco delle etichette prima di confermare.

## 3. Icona tray e menu

Il manager vive prima di tutto nella system tray. L'icona porta un badge che
segnala lo stato: nessun aggiornamento, N aggiornamenti disponibili, oppure un
errore. Il menu (rigenerato ad ogni apertura, sempre coerente con quello che
`umm list` vedrebbe in quel momento) elenca le istanze raggruppate per tipo,
ciascuna con la sua etichetta, versione, stato (in esecuzione / ferma) e le
azioni rapide — Riavvia, Ferma, Impostazioni…, Apri cartella, e Aggiorna…
quando c'è una nuova versione — più le azioni globali (Riavvia tutte, Ferma
tutte, Avvia mancanti, Controlla aggiornamenti, Apri Manager…, Esci) e i
sottomenu di profili di avvio (§10) e tipi gestiti.

## 4. Le pagine della finestra

La finestra (pywebview, HTML/CSS/JS) ha una barra laterale con cinque pagine
(`Ctrl+1`…`Ctrl+5`), un pannello **Attività** con l'esito di ogni operazione
(gli stessi messaggi compaiono come notifiche in basso a destra) e un
interruttore per il tema chiaro/scuro. `F5` aggiorna l'elenco, `Ctrl+U` avvia
il controllo aggiornamenti, `Esc` chiude pannello e menu.

- **Dashboard** — quattro riquadri (istanze in esecuzione, aggiornamenti
  disponibili, segnalazioni del watchdog, ultimo controllo) e una scheda per
  istanza: stato, versione locale con badge della versione remota, cartella di
  configurazione e file di settings effettivamente usato (clic per copiare il
  percorso), PID e uptime, data dell'exe, interruttore autostart e i pulsanti
  Riavvia/Ferma/Avvia, Impostazioni, Cartella, Ripara, Aggiorna. Il selettore
  accanto al titolo passa alla vista tabellare classica; la scelta è
  ricordata.
- **Aggiornamenti** — elenco dei tipi con confronto versione locale/ultima
  release, changelog della versione selezionata, bottone Aggiorna con log
  dell'operazione in tempo reale.
- **Impostazioni** — l'editor delle configurazioni (§6): form per chiave,
  tab JSON grezzo, storico/diff, preset colore, copia tra istanze.
- **Diagnostica** — avvio/arresto della modalità diagnostica su un'istanza e
  visualizzazione del log (§8).
- **Manager** — versione del manager e self-update, preferenze (lingua,
  canale prerelease, intervallo di controllo), token GitHub e rate limit,
  hotkey globale e file di stato, tipi gestiti (`umm kind`), profili di avvio,
  importazione configurazione precedente, log.

## 5. Aggiornamento dei monitor

### Controllare

```
umm check [--kind claude|copilot|codex|...] [--changelog] [--prerelease] [--include-skipped] [--json]
```

Stampa una tabella (tipo, exe, versione locale, ultima versione, stato,
data pubblicazione) e — con `--changelog` — le note di rilascio di ogni
release, rese in testo semplice (titoli, elenchi, link riportati come
`testo (url)`, blocchi di codice mantenuti verbatim). Esce con codice **10**
se almeno un aggiornamento è disponibile (comodo per script), **3** su errore
di rete o rate limit GitHub.

Concetto chiave: **un aggiornamento è per file exe, un riavvio è per
istanza**. Se due istanze condividono lo stesso exe (es. Claude e Claude
Valeria), aggiornarlo le riavvia entrambe con i rispettivi `--config-dir`.

### Applicare

```
umm update (--kind claude|copilot|codex|... | --all) [--yes] [--no-restart]
           [--dry-run] [--prerelease] [--include-skipped] [--allow-unverified]
           [--log FILE] [--json]
```

Il changelog viene stampato **prima** del download (come faceva lo script
PowerShell), poi una barra di progresso ridisegnata su una riga sola se
stdout è un terminale, o righe semplici altrimenti. Senza `--yes` chiede
conferma per ogni exe. `--dry-run` elenca i passi previsti senza toccare
nulla. Ogni passo è: download in `exe.download` nella stessa cartella
dell'exe → verifica SHA-256 → firma Authenticode opzionale (informativa: le
build non sono firmate) → arresto delle istanze coinvolte → `exe → exe.bak` →
`exe.download → exe` (con retry) → riavvio delle istanze che erano attive →
verifica che il processo sia vivo e la versione corrisponda.

### Verifica SHA-256

Il digest arriva dall'API GitHub (`assets[].digest`) o dal file sidecar
`.exe.sha256` quando il repo lo pubblica. Se nessuno dei due è disponibile,
l'aggiornamento non procede senza conferma esplicita:
interattivamente viene chiesto "no sha256 published; install anyway?";
in modalità non interattiva (`--yes` senza `--allow-unverified`) l'exe **non**
viene installato e il comando esce con codice **4**.

### Rollback e archivio `.umm-archive`

```
umm rollback --kind claude [--version X.Y.Z] [--no-restart] [--json]
umm archive list|prune [--kind ...] [--keep N] [--json]
```

Ogni aggiornamento mantiene, per un breve periodo, il file precedente come
`exe.bak`; `umm rollback` senza `--version` torna a quel `.bak` se ancora
presente, altrimenti alla build più recente conservata nell'archivio
`exe_dir\.umm-archive\` (con manifest: versione, data, tag di release). Con
`--version` si va a una build specifica archiviata. `umm archive list` mostra
cosa è conservato, `umm archive prune --keep N` tiene solo le N build più
recenti per exe (default configurabile nelle preferenze).

## 6. Editor delle impostazioni

Pagina **Impostazioni** della finestra, equivalente a `umm config`:

```
umm config show|validate|set|unset|create ... --instance ID [--json]
```

- **Form**: una sezione per ogni gruppo di chiavi dello schema (dichiarato
  per tipo di monitor: chiave, tipo — intero, booleano, colore hex, RGBA,
  lista di stringhe/wildcard, comando, soglie, enum — default, testo di
  aiuto). La validazione replica esattamente le regole di `_validate()` dei
  monitor, così un errore si scopre nell'editor e non nel MessageBox del
  monitor al riavvio successivo.
- **JSON grezzo**: tab con il file editabile a mano, validazione dal vivo.
- **Altre chiavi**: qualunque chiave presente nel file ma non nello schema
  documentato viene mostrata (e salvata) senza essere persa.
- **Storico, diff, ripristino**: ogni salvataggio archivia una copia datata;
  la scheda storico permette di confrontare due versioni (diff) e ripristinare
  quella scelta.
- **Preset brand**: palette pronte (Claude `#D97757`, Codex `#10A37F`,
  Copilot `#8B5CF6`, ...) che generano colore barra e icona chiara/scura in un
  clic, ripresi dal vecchio config-editor.
- **Copia tra istanze**: replica una selezione di sezioni da un'istanza a
  un'altra dello stesso tipo (es. da Claude default a Claude Valeria).
- **Salva e riavvia**: bottone dedicato che scrive il file e riavvia
  l'istanza in un solo passaggio, oltre a "Salva" semplice, "Testa quick
  action" (con conferma) e "Apri cartella".

## 7. Profili di avvio

```
umm profile list|add|remove|apply|show [NAME] [--instance ID ...] [--yes]
                                         [--dry-run] [--no-stop-others] [--json]
```

Un profilo è un insieme nominato di istanze (es. "Lavoro" = Claude+Copilot,
"Personale" = Claude Valeria+Codex). `umm profile apply NAME` avvia le
istanze del profilo e, salvo `--no-stop-others`, ferma quelle non incluse;
`--dry-run` mostra il piano senza eseguirlo. Anche nella pagina Manager e nel
sottomenu della tray.

## 8. Modalità diagnostica

```
umm diag start|stop|show [--instance ID] [--lines N] [--dry-run] [--json]
```

`umm diag start` riavvia l'istanza scelta con `--verbose`, catturandone
l'output su file; `umm diag show` (o la scheda **Diagnostica** della finestra)
ne mostra le ultime righe, comodo per copiare diagnostica quando serve aprire
un problema a monte. `umm diag stop` chiude la sessione. Senza `--instance`
il comando usa la sessione diagnostica registrata, o l'unica istanza scoperta
se ce n'è una sola.

## 9. Token GitHub e rate limit

```
umm token set|clear|status [--from-env VAR] [--json]
```

Il token è **opzionale**: senza di esso le chiamate all'API GitHub restano
anonime (limite 60 richieste/ora). `umm token set` legge il valore da stdin
(o dalla variabile d'ambiente indicata con `--from-env`) e lo salva nel
**Windows Credential Manager** (target `UsageMonitorManager/github`, letto e
scritto con `advapi32.CredReadW`/`CredWriteW` via ctypes — nessuna dipendenza
`keyring`); `manager-config.json` conserva solo il flag "presente", mai il
valore. Ordine di risoluzione: Credential Manager → env `GITHUB_TOKEN` /
`GH_TOKEN` → anonimo. `umm token status` (e la pagina Manager) mostrano se un
token è presente, la sua origine e lo stato corrente del rate limit
(`X-RateLimit-Remaining` / `Reset`), senza mai mostrare il valore stesso.

## 10. Autostart di istanze e manager

```
umm autostart (--instance ID | --manager) [on|off|status] [--json]
```

Il manager **non gestisce l'autostart dei monitor a monte** (ogni monitor
riscrive da sé la sua voce `HKCU\...\Run\...` ad ogni avvio): li **mostra**
(colonna AUTO in `umm list`, dashboard) e offre solo il toggle esplicito.
Per il manager stesso, `--manager on` scrive
`HKCU\...\Run\UsageMonitorManager` con il percorso dell'exe corrente
(risincronizzato a ogni avvio, utile se l'exe vive in OneDrive e viene
spostato); `--manager off` la rimuove; `--manager status` (o l'azione
predefinita) legge lo stato.

## 11. Hotkey globale

Nella pagina Manager, sezione "Scorciatoia e file di stato", si può impostare
una combinazione di tasti globale che apre la finestra del manager da
qualunque applicazione (campo con "cattura" per registrarla). Implementata
con `RegisterHotKey` via ctypes: nessuna dipendenza aggiuntiva come `pynput`.

## 12. File di stato per statusline

```
umm status [--check] [--write-file [PERCORSO]] [--json]
```

Scrive/stampa un documento JSON con lo stato di ogni istanza, la cronologia
degli aggiornamenti conosciuti (senza `--check` è tutto "offline": nessuna
chiamata di rete) e l'ultimo giro del watchdog. Il percorso di default è
`%LOCALAPPDATA%\UsageMonitorManager\status.json`, scritto atomicamente
(file temporaneo + rename) così un lettore che interroga il file ogni secondo
non vede mai un documento a metà. La stessa preferenza `status_file`
(pagina Manager) fa scrivere il file automaticamente dalla tray dopo ogni
ciclo di discovery/watchdog/scheduler. Schema completo, con un esempio di
statusline in JavaScript e uno in PowerShell, in
[docs/status-file.md](status-file.md).

## 13. Canale prerelease e "salta versione"

`umm check --prerelease` / `umm update --prerelease` includono le release
prerelease di GitHub nel confronto (altrimenti sono ignorate). Le preferenze
del manager (pagina Manager) possono attivare il canale prerelease in modo
permanente per un tipo. Quando una versione non interessa (es. una release
nota per essere rotta), `umm skip --kind copilot` la segna come da ignorare:
`umm check`/`umm update` la marcano `skipped` invece di riproporla, finché
non si usa `umm unskip --kind copilot` o `--include-skipped`.
`umm skip --kind manager` funziona allo stesso modo per il self-update.

## 14. Self-update del manager

```
umm self-update [--check-only] [--yes] [--dry-run] [--prerelease] [--json]
```

Stesso motore usato per i monitor, applicato a
`UsageMonitorManager.exe` stesso. Poiché un exe in esecuzione su Windows si
può rinominare ma non sovrascrivere, la sequenza è: download →
verifica → `manager.exe → manager.exe.bak` → `download → manager.exe` →
avvio del nuovo exe con `--post-update <PID vecchio>` → uscita del vecchio
processo. Il nuovo processo attende che il PID indicato termini (cioè che il
mutex singleton sia rilasciato), cancella il `.bak` e notifica l'avvenuto
aggiornamento. Il controllo automatico avviene all'avvio e periodicamente
(intervallo configurabile, disattivabile nelle preferenze); non viene mai
applicato senza un clic esplicito (o `--yes` da riga di comando).

## 15. Aggiornamento pianificato non interattivo

`umm update --all --yes --log FILE` è pensato per Task Scheduler: non chiede
mai conferma, rifiuta (uscita 4) un exe senza sha256 pubblicato invece di
fermarsi ad aspettare un input che non arriverà, e con `--log FILE` scrive un
oggetto JSON per riga per ogni passo — un log leggibile anche senza console
attaccata. Esempio di attività pianificata su Windows:

```bat
schtasks /Create /TN "Usage Monitor Manager - aggiornamenti" /SC DAILY /ST 03:30 ^
  /TR "\"D:\OneDrive\Software\Tools\AI\UsageMonitorManager.exe\" update --all --yes --log \"%LOCALAPPDATA%\UsageMonitorManager\update.log\""
```

## 16. Riferimento rapido dei comandi `umm`

| Comando | Scopo |
|---|---|
| `umm list [--json]` | tabella/JSON delle istanze scoperte |
| `umm restart / stop / start (--all \| --kind K \| --config-dir P \| --id ID) [--dry-run] [--json]` | controllo processi |
| `umm check [--kind K] [--changelog] [--prerelease] [--include-skipped] [--json]` | verifica aggiornamenti |
| `umm update (--kind K \| --all) [--yes] [--no-restart] [--dry-run] [--prerelease] [--include-skipped] [--allow-unverified] [--log FILE] [--json]` | applica aggiornamenti |
| `umm rollback --kind K [--version V] [--no-restart] [--json]` | torna a una build precedente |
| `umm archive list \| prune [--kind K] [--keep N] [--json]` | build conservate in `.umm-archive` |
| `umm skip / unskip --kind K` (`K` include `manager`) | salta una versione |
| `umm token set \| clear \| status [--from-env VAR] [--json]` | token GitHub nel Credential Manager |
| `umm config show \| validate \| set \| unset \| create ... --instance ID [--json]` | editor impostazioni da riga di comando |
| `umm self-update [--check-only] [--yes] [--dry-run] [--prerelease] [--json]` | aggiornamento del manager stesso |
| `umm status [--check] [--write-file [PATH]] [--json]` | documento di stato / statusline |
| `umm kind list \| add \| remove KEY ... [--json]` | registro dei tipi "exe da GitHub Releases" |
| `umm diag start \| stop \| show [--instance ID] [--lines N] [--dry-run] [--json]` | modalità diagnostica |
| `umm profile list \| add \| remove \| apply \| show [NAME] [--instance ID ...] [--yes] [--dry-run] [--no-stop-others] [--json]` | profili di avvio |
| `umm tray` | avvia tray + finestra (comportamento di default senza sottocomando) |
| `umm import-legacy [--dry-run] [--json]` | importazione una tantum da shortcut/Run/`profiles.json` |
| `umm autostart (--instance ID \| --manager) [on\|off\|status] [--json]` | gestione autostart |
| `--debug` | lascia passare il traceback invece del solo messaggio |
| `--post-update PID` | passo interno del self-update, non per uso manuale |
| `--version` | versione del manager |

### Codici di uscita

| Codice | Significato |
|---|---|
| 0 | tutto quello richiesto è riuscito |
| 1 | almeno un'operazione è fallita |
| 2 | comando non ancora implementato |
| 3 | nessun elemento corrisponde ai selettori, oppure GitHub irraggiungibile / rate limit |
| 4 | aggiornamento rifiutato perché l'asset non ha un sha256 pubblicato (senza `--allow-unverified`) |
| 10 | `umm check`: almeno un aggiornamento è disponibile |

Senza `--debug`, un'eccezione non gestita non mostra mai un traceback
all'utente: si vede un messaggio, un eventuale suggerimento, e il dettaglio
completo finisce comunque in `manager.log` (rotazione 1 MB × 3).

## 17. Risoluzione dei problemi

**Il file exe è "in uso" durante l'aggiornamento (OneDrive o antivirus).**
Molte installazioni tengono gli exe dentro OneDrive; il client di sync o un
antivirus possono trattenere brevemente il lock sul file al momento dello
swap. Il manager ritenta il rename con backoff (5 tentativi, 2 secondi di
attesa) e, se anche così non riesce, ripristina automaticamente `exe.bak`
senza lasciare l'installazione a metà. Se il problema persiste: sospendere
temporaneamente la sincronizzazione di OneDrive, o chiudere il file, e
ripetere `umm update`.

**Rate limit GitHub.** Senza token le richieste anonime sono limitate a
60/ora; con più tipi controllati insieme (e il self-update del manager) il
limite si può esaurire. Sintomi: `umm check`/`umm update` escono con codice 3
e un messaggio che indica l'orario di reset. Soluzioni: aspettare il reset,
oppure impostare un token con `umm token set` (§9) — la cache ETag riduce
comunque le richieste ripetute allo stretto necessario.

**Popup "Failed to load Python DLL".** Capita quando un processo
PyInstaller ne avvia un altro ereditando per errore le variabili d'ambiente
`_PYI_*` / `_MEI*` del proprio bundle: il figlio cerca le librerie nella
cartella temporanea del padre, che non esiste più o non gli appartiene. Il
manager evita questo esattamente: quando avvia o riavvia un'istanza, l'ambiente
del processo figlio **non eredita mai** queste variabili né, per il config
dir di default, la variabile d'ambiente specifica del tipo (es.
`CLAUDE_CONFIG_DIR`), che viene invece impostata esplicitamente solo quando
serve. Se il popup compare comunque avviando un'istanza manualmente da un
terminale che ha già queste variabili nell'ambiente (ad es. una shell di
sviluppo), il problema è nell'ambiente di quel terminale, non nel manager:
apri un nuovo terminale "pulito", o avvia l'istanza dal manager stesso.

**Un'istanza non parte perché il mutex è ancora tenuto.** Il manager
considera un'istanza "fermata" solo quando il suo mutex singleton
(`UsageMonitorForXxx_SingleInstance<suffisso>`) risulta effettivamente
rilasciato, non semplicemente quando i processi non sono più nella lista.
Se un arresto lascia il mutex occupato per qualche istante (bootloader lento
a chiudersi), il successivo `start` si rifiuta di avviare una seconda copia
invece di creare un duplicato: attendere pochi secondi e ritentare, oppure
controllare con `umm list` che i PID indicati siano davvero spariti.

**File dei settings non trovato.** Ogni monitor cerca
`usage-monitor-settings.json` in un ordine preciso, che il manager replica
esattamente per capire quale file un'istanza sta davvero usando:

1. `$CONFIG_DIR/` — solo se il config dir non è quello di default del tipo;
2. accanto all'eseguibile (`exe_dir/`);
3. il config dir di default del tipo (`~/.claude`, `~/.copilot`, `~/.codex`).

Il manager non crea mai il file da solo dietro le quinte: se nessuna delle
tre posizioni esiste, la dashboard lo segnala e l'editor propone il pulsante
"Crea file" nella posizione corretta della search order. Modificare un file
nella posizione sbagliata (es. accanto all'exe quando il monitor legge quello
nel config dir) non ha alcun effetto sul monitor: la dashboard mostra sempre
quale delle tre posizioni è quella "attiva" e quali sono solo "in ombra".

**Settings indipendenti per un'istanza.** Se un'istanza con cartella di
configurazione personalizzata (per esempio `.claude2`) usa il file accanto
all'eseguibile o quello della cartella predefinita, aprire **Impostazioni →
Altro → Crea settings indipendenti**. Il manager salva i valori attualmente
nell'editor, comprese le modifiche non ancora salvate, in
`<config_dir>/usage-monitor-settings.json`, lasciando intatto il file di
origine. I salvataggi successivi riguardano la copia dell'istanza. Riavviare
l'istanza per farle leggere il nuovo file. L'azione è disponibile solo se
la copia non esiste già e la cartella è personalizzata: per quella predefinita
il monitor continua a dare precedenza al file accanto all'eseguibile.
