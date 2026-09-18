# usage-monitor-updater — archiviato

> **Questo repository è archiviato.** È stato sostituito da
> [`usage-monitor-manager`](https://github.com/hybrid2102/usage-monitor-manager),
> che copre lo stesso compito e molto altro.

## Perché

`usage-monitor-manager` fa tutto quello che faceva questo script PowerShell —
confronto della versione locale (PE `ProductVersion`) con l'ultima release
GitHub, changelog mostrato durante il download, download e verifica
SHA-256 dell'asset, sostituzione dell'eseguibile con backup (`.bak`) e
ripristino automatico in caso di errore, riavvio di ogni istanza che era in
esecuzione (incluse le istanze aggiuntive definite dagli shortcut
`<exe> - *.lnk`) — con in più:

- **auto-discovery** delle istanze in esecuzione (processi, Run key,
  shortcut, `profiles.json` del config-editor), senza dover elencare a mano
  le installazioni;
- **rollback e archivio** delle build precedenti (`.umm-archive`), non solo
  l'ultimo `.bak`;
- **canale prerelease** e possibilità di **saltare una versione** (`umm skip`)
  senza che venga riproposta ad ogni check;
- **editor delle impostazioni** integrato (form, storico/diff, preset di
  colore) — quello che faceva `usage-monitor-config-editor`, anch'esso
  archiviato;
- **tray e finestra** oltre alla riga di comando, più **self-update** del
  manager stesso;
- **modalità pianificata** equivalente (`umm update --all --yes --log FILE`),
  compatibile con lo stesso uso da Task Scheduler dello `-NonInteractive` di
  questo script.

## Ultima versione funzionante

L'ultima versione di questo script, **v1.1.1**, resta consultabile nella
cronologia delle release di questo repository e continua a funzionare per chi
non è ancora passato al manager.

Il modulo **PowerShell Gallery `Update-UsageMonitor` resta invariato**: non è
interessato da questa archiviazione e continua a essere pubblicato e
mantenuto come prima.

## Come migrare

1. Scarica `UsageMonitorManager.exe` dalle release di
   [`usage-monitor-manager`](https://github.com/hybrid2102/usage-monitor-manager)
   e posizionalo dove preferisci (nessun installer).
2. Avvialo una volta: al primo avvio propone di importare gli shortcut
   `<exe> - *.lnk` esistenti (le stesse istanze aggiuntive che questo script
   gestiva) — vedi `umm import-legacy --dry-run` per una simulazione.
3. Da quel momento, `umm check` / `umm update --all` (o la scheda
   "Aggiornamenti" della finestra) sostituiscono l'invocazione di questo
   script o della relativa attività pianificata; se usavi Task Scheduler,
   aggiorna la voce per puntare a
   `UsageMonitorManager.exe update --all --yes --log <percorso log>`.
4. Nessuna configurazione da migrare per l'updater in sé: non aveva stato
   persistente oltre agli shortcut, già importati al passo 2.

---

# usage-monitor-updater — archived

> **This repository is archived.** It has been replaced by
> [`usage-monitor-manager`](https://github.com/hybrid2102/usage-monitor-manager),
> which covers the same job and a lot more.

## Why

`usage-monitor-manager` does everything this PowerShell script did — compare
the local version (PE `ProductVersion`) against the latest GitHub release,
show the changelog while downloading, download and verify the asset's
SHA-256, swap the executable with a backup (`.bak`) and roll back
automatically on failure, restart every instance that was running (including
the extra instances defined by `<exe> - *.lnk` shortcuts) — plus:

- **auto-discovery** of running instances (processes, Run key, shortcuts,
  the config-editor's `profiles.json`), no need to list installations by
  hand;
- **rollback and an archive** of previous builds (`.umm-archive`), not just
  the last `.bak`;
- a **prerelease channel** and the ability to **skip a version** (`umm skip`)
  so it is not offered again at every check;
- a built-in **settings editor** (form, history/diff, color presets) — what
  `usage-monitor-config-editor` used to do, also archived;
- a **tray and window**, on top of the command line, plus **self-update**
  for the manager itself;
- an equivalent **scheduled mode** (`umm update --all --yes --log FILE`),
  compatible with the same Task Scheduler use as this script's
  `-NonInteractive`.

## Last working version

The last version of this script, **v1.1.1**, remains available in this
repository's release history and keeps working for anyone who has not moved
to the manager yet.

The **`Update-UsageMonitor` PowerShell Gallery module stays as-is**: it is
not affected by this archival and continues to be published and maintained
as before.

## Migration steps

1. Download `UsageMonitorManager.exe` from the
   [`usage-monitor-manager`](https://github.com/hybrid2102/usage-monitor-manager)
   releases and place it wherever you like (no installer).
2. Start it once: on first launch it offers to import any existing
   `<exe> - *.lnk` shortcuts (the same extra instances this script managed) —
   see `umm import-legacy --dry-run` for a preview.
3. From then on, `umm check` / `umm update --all` (or the "Updates" tab in
   the window) replace running this script or its scheduled task; if you
   used Task Scheduler, update the entry to point at
   `UsageMonitorManager.exe update --all --yes --log <log path>`.
4. There is no configuration to migrate for the updater itself: it had no
   persistent state beyond the shortcuts, already imported in step 2.
