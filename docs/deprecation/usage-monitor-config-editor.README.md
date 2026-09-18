# usage-monitor-config-editor — archiviato

> **Questo repository è archiviato.** È stato sostituito dalla pagina
> "Impostazioni" di
> [`usage-monitor-manager`](https://github.com/hybrid2102/usage-monitor-manager).

## Perché

La pagina Impostazioni del manager copre tutte le chiavi documentate dei tre
monitor (Claude, Copilot, Codex) — non solo le tre che editava questa
applicazione (`bar_fg`, i colori dell'icona, `quick_action_command`) — e
aggiunge quello che qui mancava:

- **tutte le chiavi documentate** per tipo, con form generato da uno schema
  dichiarativo e validazione che replica esattamente le regole
  `_validate()` dei monitor, invece del solo tab JSON grezzo di questa app;
- **conserva le chiavi non gestite**: qualunque chiave presente nel file ma
  non nello schema resta e viene salvata, esattamente come faceva questa
  applicazione, con salvataggio atomico e `.bak`;
- **storico e diff**: ogni salvataggio archivia una copia datata, con diff e
  ripristino — questa applicazione non aveva alcuna cronologia;
- **preset di colore** brand-aware (Claude, Codex, Copilot, ...), stessa
  idea di questa applicazione ma integrata nel form invece che come
  strumento separato;
- **riavvio dell'istanza** dopo il salvataggio, con un bottone dedicato
  "Salva e riavvia" — questa applicazione non riavviava mai nulla.

## Ultima versione

L'ultima versione di questo repository, **v0.2.0** (EXE PySide6, ~89 MB),
resta consultabile nella cronologia delle release per chi non è ancora
passato al manager.

## Come migrare

1. Scarica `UsageMonitorManager.exe` dalle release di
   [`usage-monitor-manager`](https://github.com/hybrid2102/usage-monitor-manager).
2. Al primo avvio, il manager importa automaticamente
   `%LOCALAPPDATA%\UsageMonitorConfigEditor\profiles.json` — il registro dei
   profili di questa applicazione — proponendo le stesse etichette già in
   uso (es. "Claude Valeria"). Nessun file di questa applicazione viene
   modificato o rimosso: l'importazione è di sola lettura sulla fonte.
3. Da quel momento, apri la scheda "Impostazioni" della finestra del manager
   (o `umm config show|set|... --instance ID` da riga di comando) al posto
   di questa applicazione per modificare i file
   `usage-monitor-settings.json`.
4. Nessun'altra configurazione da migrare: questa applicazione non teneva
   altro stato persistente oltre a `profiles.json`, già importato al passo 2.

---

# usage-monitor-config-editor — archived

> **This repository is archived.** It has been replaced by the "Settings"
> page of
> [`usage-monitor-manager`](https://github.com/hybrid2102/usage-monitor-manager).

## Why

The manager's Settings page covers every documented key of the three
monitors (Claude, Copilot, Codex) — not just the three this app edited
(`bar_fg`, the icon colors, `quick_action_command`) — and adds what this app
was missing:

- **every documented key** per monitor type, with a form generated from a
  declarative schema and validation that mirrors the monitors' own
  `_validate()` rules exactly, instead of this app's raw-JSON-only tab;
- **preserves unmanaged keys**: any key present in the file but not in the
  schema is kept and saved, exactly as this app did, with atomic save and a
  `.bak`;
- **history and diff**: every save archives a dated copy, with diff and
  restore — this app had no history at all;
- **brand-aware color presets** (Claude, Codex, Copilot, ...), the same idea
  this app had but built into the form instead of a separate tool;
- **restarting the instance** after saving, with a dedicated "Save and
  restart" button — this app never restarted anything.

## Last version

The last version of this repository, **v0.2.0** (PySide6 EXE, ~89 MB),
remains available in the release history for anyone who has not moved to the
manager yet.

## Migration steps

1. Download `UsageMonitorManager.exe` from the
   [`usage-monitor-manager`](https://github.com/hybrid2102/usage-monitor-manager)
   releases.
2. On first launch, the manager automatically imports
   `%LOCALAPPDATA%\UsageMonitorConfigEditor\profiles.json` — this app's
   profile registry — suggesting the same labels already in use (e.g.
   "Claude Valeria"). No file belonging to this app is modified or removed:
   the import is read-only on the source.
3. From then on, open the "Settings" tab of the manager's window (or
   `umm config show|set|... --instance ID` from the command line) instead of
   this app to edit the `usage-monitor-settings.json` files.
4. There is nothing else to migrate: this app kept no other persistent state
   beyond `profiles.json`, already imported in step 2.
