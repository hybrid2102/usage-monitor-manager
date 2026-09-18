# Architettura

Riferimento: [PLAN.md](../PLAN.md) §4 e §6. Questo documento descrive il codice
**come è stato costruito**, non come era stato previsto: dove i due divergono,
vale quanto scritto qui.

## Mappa dei moduli

```
src/umm_launcher.py          entry point del build congelato (importa il package per nome assoluto)
src/usage_monitor_manager/
  __init__.py                __version__
  __main__.py                python -m usage_monitor_manager -> CLI (tray se non si passa nulla)
  cli.py                     argparse, tabelle, codici di uscita, politica degli errori
  i18n.py                    catalogo T[chiave] su locale/it.json e locale/en.json
  errors.py                  UmmError(code, message, hint) e la mappatura sui codici di uscita
  kinds.py                   registro dei tipi (dati): famiglie usage-monitor e github-exe
  model.py                   MonitorKind, Instance, ProcessPair, ReleaseInfo, ReleaseAsset, UpdatePlan
  identity.py                config_dir_suffix: la stessa sha1 dei monitor, per mutex e Run key
  discovery.py               scan processi + Run key + shortcut + settings + exe scan -> list[Instance]
  process_control.py         stop / start / restart, ambiente del figlio, mutex singleton
  versions.py                versione PE, parsing e confronto, stati up_to_date/update_available/...
  github.py                  API releases, cache ETag, rate limit, scelta asset, download con progress
  integrity.py               sha256 (digest API o sidecar), stato Authenticode (informativo)
  updater.py                 UpdatePlan -> stop, swap con .bak, restart, verifica, rollback
  archive.py                 .umm-archive: build conservate, manifest, keep-N, restore
  self_update.py             lo stesso engine applicato al manager (rename-swap + rilancio)
  secrets.py                 token GitHub nel Windows Credential Manager (ctypes advapi32)
  scheduler.py               controllo periodico, ultimo controllo per repo, una notifica per (repo, tag)
  watchdog.py                crashed / duplicate / orphan, registro "expected down", riparazioni
  diag.py                    modalita diagnostica (--verbose su file) e manager.log rotante
  status_file.py             il documento di stato e la sua scrittura atomica (status.json)
  profiles.py                profili di avvio: piano puro + applicazione
  legacy.py                  importazione una tantum da shortcut, Run key e profiles.json
  manager_config.py          manager-config.json: preferenze, istanze dichiarate, custom_kinds, profili
  settings/
    schema.py                schema per kind (chiavi, tipo, default, sezione, help)
    store.py                 load/validate/save atomico + .bak, chiavi sconosciute preservate
    presets.py               palette brand (dal vecchio config-editor)
  history.py                 storico datato dei settings, diff, export/import
  platform/
    __init__.py              dispatcher su sys.platform
    win32.py                 versione PE, ProductName, Run key, shortcut, mutex, Authenticode, hotkey
    linux.py                 stub documentato (vedi sotto)
  locale/it.json, en.json    cataloghi di traduzione
  ui/
    tray.py                  icona, menu, notifiche, singleton, hotkey, scrittura di status.json
    window.py                JsApi (client puro del core) + avvio di pywebview
    web/                     index.html, app.css, app.js (nessun framework, nessun build step)
```

Regola strutturale: **tutta la logica sta nel core, la UI chiama solo funzioni
del core.** `JsApi` non contiene logica di dominio e si può testare senza
webview; la tray non fa altro che comporre etichette e chiamare `JsApi`.
È la mitigazione di §8 «regressioni UI difficili da testare».

## Un solo percorso di avvio

Esiste **una** funzione che lancia un monitor: `process_control.start`. Ci
arrivano la CLI (`umm start|restart`), la tray, la finestra, l'updater (che
riavvia le istanze fermate), il watchdog (che ripara) e la modalità diagnostica
(che passa `--verbose` e un file di log). Nessun altro modulo costruisce un
`Popen` di un monitor.

Lo stesso vale per lo stop: `process_control.stop`, con il proprio controllo
del mutex.

Il motivo è la lezione della fase 2: un percorso di avvio alternativo eredita
l'ambiente di chi lo chiama, e con esso `CLAUDE_CONFIG_DIR` e le variabili
`_PYI_*` del bundle PyInstaller del padre — il monitor parte sulla config dir
sbagliata, o non parte affatto (*Failed to load Python DLL*).

## Invarianti di sicurezza

Sono le regole che i test difendono esplicitamente; valgono per ogni chiamante.

**Controllo processi**

1. si termina solo un PID il cui `exe()` coincide (`os.path.normcase`) con
   quello dell'istanza, verificato di nuovo subito prima di `terminate()` e di
   `kill()`; mai per nome del processo, e **mai** il processo corrente o un suo
   antenato;
2. «fermata» significa **mutex rilasciato**: finché il singleton risulta
   occupato `stop` non dichiara successo e `start` si rifiuta di partire;
3. l'ambiente del figlio non eredita mai le variabili `_PYI_*` / `_MEI*` del
   padre né la env var del kind: viene rimossa per la config dir di default e
   impostata esplicitamente per le altre;
4. due istanze dello stesso eseguibile non vengono avviate insieme: due secondi
   di distanza (WebView2).

**Aggiornamenti**

5. si aggiorna **un exe**, si riavvia **un'istanza**: il piano ferma e riavvia
   tutte le istanze che usano quel file, e solo quelle che erano in esecuzione;
6. l'exe nuovo arriva nella stessa cartella del vecchio (`.download`), così lo
   swap è un rename; il `.bak` resta finché il controllo post-avvio non riesce;
7. sha256 mancante = update rifiutato salvo conferma esplicita (uscita 4 in
   modalità non presidiata); sha256 diverso = update annullato e file scaricato
   cancellato, senza toccare l'exe;
8. le operazioni del manager si annunciano al registro «expected down» del
   watchdog, così un suo stop non viene mai scambiato per un crash.

**Stato e file dell'utente**

9. il manager scrive solo in `%LOCALAPPDATA%\UsageMonitorManager\` e nei file
   di settings che l'utente gli chiede di modificare; i log della diagnostica
   stanno sotto la sua directory, mai in una config dir;
10. ogni scrittura di un file dell'utente è atomica (file temporaneo + rename)
    e lascia un `.bak`; le chiavi sconosciute di un file di settings sono
    **conservate**;
11. il manager non gestisce l'autostart dei monitor: lo **mostra** e offre solo
    un toggle esplicito, perché il monitor riscrive la propria Run key ad ogni
    avvio;
12. il token GitHub non passa mai da `argv` e non viene mai ristampato.

**Profili**

13. un profilo ferma solo la famiglia `usage-monitor`: un `github-exe` fuori dal
    profilo resta in esecuzione e viene segnalato.

## Errori e log

`errors.py` tiene la gerarchia `UmmError(code, message, hint)`: il codice di
uscita appartiene all'errore, non al punto di chiamata. La CLI stampa
`message` e `hint` su stderr ed esce con `code`; la tray mostra `message` in una
notifica; la finestra risponde alla pagina `{"ok": false, "detail", "hint"}`
grazie al decoratore `@errors.guarded`. Un'eccezione **non** prevista è un bug:
finisce su `logs\manager.log` con il traceback e all'utente arriva una riga
sola. `--debug` disattiva la rete di sicurezza e lascia passare il traceback.

`manager.log` è un file rotante da 1 MB × 3 in
`%LOCALAPPDATA%\UsageMonitorManager\logs\`, a cui scrivono tray, finestra,
scheduler, watchdog, updater e diagnostica.

## Traduzioni

`i18n.py` carica un JSON piatto per lingua da `locale/`. `T["chiave"]` non
solleva mai: una chiave senza traduzione ritorna se stessa, così un catalogo
rimasto indietro non fa cadere la finestra. Il test statico
(`tests/test_i18n.py`) verifica che i due cataloghi abbiano le stesse chiavi e
gli stessi segnaposto e che ogni chiave usata nei `.py`, nei `.js` e negli
attributi `data-i18n` dell'HTML esista davvero. La pagina riceve tutto il
catalogo in una sola chiamata (`JsApi.get_translations`).

## Il layer `platform/` e cosa servirebbe per Linux

`platform/__init__.py` fa dispatch su `sys.platform`. `win32.py` è
l'implementazione reale; `linux.py` è uno **stub documentato** (PLAN.md §9.12,
decisione 5 del §10: solo predisposizione architetturale). Ogni primitiva che
richiederebbe codice Linux solleva
`NotImplementedError("Linux: previsto, non implementato ...")`: uno stub che
solleva è onesto, uno che restituisce un dizionario vuoto farebbe trovare alla
discovery «nessuna istanza» senza dire perché. Tre funzioni non sollevano,
perché su POSIX la risposta banale è quella giusta:
`attach_parent_console` (il processo ha già il terminale del padre),
`no_window_popen_kwargs` (non ci sono finestre di console da nascondere) e
`authenticode_status` (non esiste Authenticode, e il chiamante tratta già la
risposta come informativa).

Una porta reale dovrebbe costruire:

| Area | Su Windows | Su Linux servirebbe |
|---|---|---|
| Singleton | mutex con nome (`CreateMutexW` + `ERROR_ALREADY_EXISTS`) | **`flock`** su un file in `$XDG_RUNTIME_DIR`; «fermata» diventa «lock rilasciato» |
| Versione dell'eseguibile | risorsa PE `ProductVersion` | i monitor girano **da sorgente**: versione da `pyproject`/`__version__` del checkout, non da un file binario |
| Identità del kind | PE `ProductName` | nome dello script o del pacchetto; il `ProductName` non esiste |
| Autostart | `HKCU\...\Run` | file `.desktop` in `~/.config/autostart/` |
| Percorsi | `%LOCALAPPDATA%`, `%APPDATA%` | **XDG**: `$XDG_STATE_HOME`, `$XDG_CONFIG_HOME`, `$XDG_RUNTIME_DIR` |
| Shortcut legacy | `.lnk` letti via `WScript.Shell` | niente da importare: le istanze si dichiarano in `manager-config.json` |
| Aggiornamento | scarica l'exe, `.bak`, rename | **run from source**: `git pull` / reinstallazione del pacchetto, non uno swap di file; l'archivio delle build perde senso |
| Finestra senza console | `DETACHED_PROCESS | CREATE_NO_WINDOW` | `start_new_session=True` |
| Hotkey globale | `RegisterHotKey` + loop `GetMessageW` | integrazione con il display server (X11 `XGrabKey`, o il portal su Wayland): è la parte meno portabile |
| Firma | `WinVerifyTrust` (informativo) | niente equivalente; resta `unavailable` |

La discovery e il controllo processi sono invece già portabili: psutil, il
collasso delle coppie padre/figlio e le regole di sicurezza sui PID non hanno
nulla di specifico di Windows.
