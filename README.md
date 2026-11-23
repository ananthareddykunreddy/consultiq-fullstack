# ConsultIQ Full-Stack

Portale FastAPI per ConsultIQ con area pubblica, area cliente, area admin, upload documenti, audit log, notifiche e strumenti operativi per servizi CAF/Patronato.

## Setup locale

1. Crea e attiva un virtualenv Python 3.10+.
2. Installa le dipendenze:
   ```bash
   pip install -r requirements.txt
   ```
3. Copia i valori necessari da `.env.example`.
4. Avvia il server:
   ```bash
   python -m uvicorn app.main:app --host 127.0.0.1 --port 8011
   ```
5. Verifica:
   - `http://127.0.0.1:8011/`
   - `http://127.0.0.1:8011/health`

## Variabili ambiente

- `CONSULTIQ_SESSION_SECRET`
- `CONSULTIQ_ADMIN_EMAIL`
- `CONSULTIQ_ADMIN_PASSWORD`
- `CONSULTIQ_MAX_UPLOAD_BYTES`
- `CONSULTIQ_DB_PATH`
- `CONSULTIQ_SMTP_HOST`
- `CONSULTIQ_SMTP_PORT`
- `SMTP_USER`
- `SMTP_PASSWORD`
- `SMTP_FROM_EMAIL`
- `SMTP_FROM_NAME`
- `ADMIN_NOTIFICATION_EMAIL`
- `CLAMAV_ENABLED`
- `CLAMAV_FAIL_MODE`
- `WHATSAPP_BUSINESS_NUMBER`

## Funzionalita' attuali

- Homepage, servizi, news, contatti, area cliente, area admin
- Prenotazioni con consenso privacy/termini e upload documento opzionale
- Banner cookie con preferenze locali e blocco analytics fino a consenso
- Placeholder legali per privacy, cookie policy, termini e nota GDPR
- Upload documenti con categorie, stato, note cliente, scadenza e scan status
- Accesso documenti tramite route protetta con audit log
- Export dati cliente e anonimizzazione da area admin
- Aggiornamento stato pratica, stato pagamento e notifiche email da admin
- Notifiche email resilienti: se SMTP manca, il sistema non va in crash
- Header di sicurezza HTTP, rate limiting, CSRF, login admin con 2FA
- `robots.txt` e `sitemap.xml`

## SMTP email

- Configura i campi SMTP per inviare email reali.
- Se SMTP non e' configurato, le notifiche vengono salvate in coda come `pending` o `failed`.
- L'admin dashboard mostra un avviso esplicito quando l'invio reale non e' disponibile.

## Deployment cPanel

1. Clona il repository da GitHub in `~/consultiq-fullstack`.
2. Crea l'app Python in cPanel con root `consultiq-fullstack`.
3. Installa i pacchetti con `pip install -r requirements.txt`.
4. Imposta le variabili ambiente del file `.env.example`.
5. Assicurati che `app/data` e `app/data/uploads` siano scrivibili.
6. Riavvia l'app dal pannello Python di cPanel.

## Note di sicurezza

- Cambia subito `CONSULTIQ_SESSION_SECRET` e password admin default.
- Mantieni SMTP e altri segreti solo in ambiente, mai nel repository.
- Verifica CSP e domini consentiti prima del go-live.
- Controlla periodicamente audit log, export dati e flussi di anonimizzazione.
- Completa backup automatici database e cartella upload.
- Non abilitare gateway di pagamento live finche' il flusso manuale non e' validato e approvato.

## Checklist GDPR / legale

- Verifica testi Privacy, Cookie Policy, Termini e Nota GDPR con un consulente legale.
- Definisci tempi di retention per ogni categoria di pratica.
- Conferma procedura interna per export, cancellazione e anonimizzazione.
- Valuta nomine responsabili trattamento, registro trattamenti e procedure data breach.
- Valida cookie reali presenti in produzione.

## Guida admin

- `/admin` mostra KPI, notifiche, audit, appuntamenti, documenti e utenti.
- Puoi aggiornare stato pratica e pagamento, chiedere documenti mancanti e inviare email operative.
- Puoi eliminare appuntamenti/documenti, esportare dati cliente e anonimizzare utenti.
- I documenti scaricati via dashboard passano da una route protetta con audit log.

## Guida cliente

- Registrazione/login per vedere pratiche e documenti.
- Upload documento per appuntamento con categoria e data di scadenza.
- Visualizzazione stato documenti, note operative e notifiche.

## Backup consigliato

- Backup giornaliero del file SQLite.
- Backup della cartella `app/data/uploads`.
- Conservazione esterna separata dal server pubblico.

## Checklist lancio ConsultIQ

- Verifica `/admin/launch-readiness` per deploy, migrazioni, backup e note legali.
- Verifica `/admin/permissions` per controllare i permessi di admin, responsabili, operatori e clienti.
- Verifica `/admin/document-reminders` per documenti in scadenza e promemoria.
- Usa `/admin/audit/export` per esportare il registro audit in CSV.
- Usa il pulsante auto-assegnazione in dashboard solo dopo aver creato operatori attivi.
- Conferma SMTP prima del go-live: senza SMTP le notifiche restano in coda ma il sito non va in crash.
