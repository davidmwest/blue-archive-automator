# the whole day's log

click **full day's log** next to **important actions**. it opens the day's text log in a new tab, where the browser's find and save commands work normally. refresh that tab for the latest entries.

Logs are saved under `state_dir/logs/YYYY-MM-DD.log`, normally `data/state/logs/2026-09-24.log`. The date follows the computer's local calendar, and each timestamp includes its UTC offset. Midnight starts a new file. Restarting the dashboard appends to the existing file; clearing loot or dismissing a failed-job notice does not erase it.

The log includes:

- Queue submissions, dispatch, cancellation, pause/resume, and job results.
- Recognized task states, intended taps and swipes, input outcomes, checks, and failures.
- Important actions, received item names and quantities, spending results, and evidence references.
- Schedule changes, setting changes, startup/recovery diagnostics, and app closure.

The dashboard's compact log can show only a recent buffer. The daily file contains the full retained day, including work from separate task processes. Screenshots and raw OCR remain in their existing local evidence files; payment and authentication details are omitted from this text view.

Existing task journals and important actions can be imported without repeating entries. Older journals recorded elapsed time, so their reconstructed timestamps are explicitly marked `timestamp_estimated=true`. New journal entries have their own timestamps and event identities. Importing old records preserves chronological order and existing daily entries.

Read-only endpoints:

- `/api/logs/today.log` opens today's text with its dated filename.
- `/api/logs/YYYY-MM-DD.log` opens a saved day.
- `/api/logs` lists available dates.

The endpoints use the same loopback-only access rules as the dashboard and accept dates rather than filesystem paths.
