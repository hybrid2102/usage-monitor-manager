# `status.json` — API di stato locale

Riferimento: [PLAN.md](../PLAN.md) §9.11.

Il manager può tenere aggiornato un file JSON con lo stato delle istanze, così
che una statusline, un prompt di shell o uno script qualsiasi possano leggerlo
**senza avviare un processo Python** e senza fare alcuna chiamata di rete.

È lo stesso documento che stampa `umm status --json`.

## Come si attiva

Tre modi, tutti equivalenti quanto a contenuto:

| Modo | Quando viene scritto |
|---|---|
| preferenza `status_file` (pagina Manager, o `manager-config.json`) | dalla tray, dopo ogni ciclo di discovery, watchdog e scheduler |
| `umm status --write-file` | una volta, sul percorso di default |
| `umm status --write-file PERCORSO` | una volta, dove indicato |

Il percorso di default è
`%LOCALAPPDATA%\UsageMonitorManager\status.json`. Se il valore indicato è una
directory (esistente, o scritta con lo separatore finale) il nome
`status.json` viene aggiunto. La preferenza vale `null` di default: il manager
non scrive **nessun** file che non sia stato chiesto.

La scrittura è **atomica**: il documento viene scritto in un file temporaneo
nella stessa directory e poi rinominato sopra il bersaglio, quindi un lettore
che gira ogni secondo vede sempre il documento precedente o quello nuovo, mai
mezzo file.

## Schema

Tutte le chiavi sono sempre presenti: un consumatore non deve mai controllare
se una chiave esiste, solo se il valore è `null`.

```jsonc
{
  "version": "0.8.0",                        // versione del manager che ha scritto il file
  "generated_at": "2026-09-18T12:13:12.512Z", // ISO 8601 con timezone: serve a capire se il file è vecchio
  "running": 4,                               // quante istanze risultano in esecuzione
  "instances": [
    {
      "id": "claude_578560bbc610",           // id stabile, lo stesso di `umm list`
      "kind": "claude",
      "family": "usage-monitor",             // "usage-monitor" | "github-exe"
      "label": "Claude Valeria",             // etichetta utente, o quella suggerita
      "config_dir": "C:\\Users\\Marco\\.claude-valeria", // null per la famiglia github-exe
      "exe_path": "D:\\...\\UsageMonitorForClaude.exe",  // null se non determinabile
      "local_version": "1.22.0",             // 3 componenti, null se l'exe non ha risorsa versione
      "is_running": true,
      "pid": 4512                            // pid dell'app (non del bootloader), null se ferma
    }
  ],
  "updates": {
    "checked": false,                        // true solo con --check
    "last_check": {                          // per repo, memorizzato dallo scheduler
      "hybrid2102/usage-monitor-for-copilot": "2026-09-18T09:00:00+00:00"
    },
    "skipped_versions": {                    // versioni che l'utente ha chiesto di saltare
      "hybrid2102/usage-monitor-for-copilot": "v0.3.2"
    },
    "plans": [                               // vuoto senza --check
      {
        "kind": "copilot",
        "exe_path": "D:\\...\\UsageMonitorForCopilot.exe",
        "current_version": "0.3.1",
        "target_version": "0.3.2",
        "tag": "v0.3.2",
        "status": "update_available"         // up_to_date | update_available | newer_local | unknown | skipped
      }
    ],
    "available": 1,                          // aggiornamenti non saltati; 0 senza --check
    "error": null                            // messaggio se GitHub non ha risposto (rate limit, rete)
  },
  "self_update": null,                       // stato dell'aggiornamento del manager; null senza --check
  "watchdog": {
    "last_poll": "2026-09-18T12:13:12+00:00",
    "count": 0,
    "events": []                             // {"type": "crashed|duplicate|orphan", "instance_id": ..., "detail": ...}
  }
}
```

Note per chi lo consuma:

- **senza `--check` il documento è offline.** `updates.checked` è `false`,
  `plans` è vuoto, `available` è `0` e `self_update` è `null`. Quello che
  GitHub ha risposto l'ultima volta sta in `updates.last_check`: è la lettura
  giusta per una statusline, che non deve interrogare l'API a ogni prompt.
- **`generated_at` è l'unico modo di accorgersi che il manager non gira più.**
  Un file più vecchio di qualche minuto, con la tray attiva, significa che la
  tray non c'è: conviene mostrare uno stato "sconosciuto" invece dei numeri
  vecchi.
- un errore di scrittura non ferma il manager: la tray lo ignora e riprova al
  ciclo dopo. `umm status --write-file` invece esce **1** e stampa il motivo.

## Esempio: statusline

```js
// statusline.mjs - riquadro "monitor" da un file, senza processi e senza rete
import { readFileSync } from "node:fs";

const PATH = `${process.env.LOCALAPPDATA}\\UsageMonitorManager\\status.json`;
const STALE_MS = 5 * 60 * 1000;

function monitors() {
  let status;
  try {
    status = JSON.parse(readFileSync(PATH, "utf8"));
  } catch {
    return "monitor ?"; // file assente: il manager non ha mai scritto
  }
  const age = Date.now() - Date.parse(status.generated_at);
  if (age > STALE_MS) return "monitor ? (stato vecchio)";

  const total = status.instances.length;
  const down = status.instances.filter((i) => !i.is_running).map((i) => i.kind);
  const updates = status.updates.available;

  let text = `monitor ${status.running}/${total}`;
  if (down.length) text += ` (giu: ${down.join(", ")})`;
  if (updates) text += ` | ${updates} update`;
  if (status.watchdog.count) text += ` | ! ${status.watchdog.count}`;
  return text;
}

console.log(monitors());
```

Equivalente in PowerShell, per un prompt:

```powershell
$s = Get-Content "$env:LOCALAPPDATA\UsageMonitorManager\status.json" -Raw | ConvertFrom-Json
"{0}/{1} monitor attivi" -f $s.running, $s.instances.Count
```
