# View > Telemetry

The main web console lists satellites whose configuration enables one of the
generic, MONITOR, MARMOTSat, LASARsat, OTP-2 or RS-61 telemetry decoders.
Selecting a satellite opens a read-only graphical dialog styled like the Radio
and QTH editors, in either the classic or modern theme.
Satellites with no packets in the viewer's recent history appear grey and italic
in the menu. They remain selectable so the console can wait for live reception.
Availability is checked again when returning to the menu; unreadable archives
are not marked empty.

The dialog shows packet source, decode time, format/validation, decoded fields,
raw packet metadata and the log file path. It refreshes every three seconds
while open. Select an earlier packet in Packet history to keep it displayed;
select Latest packet to follow incoming packets. Unknown payloads are clearly
labelled rather than displayed as invented measurements.

Each numeric field has a small time-series graph to the right of its value
(below it on narrow screens). Graphs use the loaded history, with actual local
decode times on the horizontal axis and an independent value scale per field.
The range and sample count appear above each graph. Hover over a dot for the
exact time and value; the amber dot marks the selected packet. Single samples
appear as one dot and constant readings form a flat line. Missing or nonnumeric
values leave gaps, and separate record types and sources are not mixed.

Opening this viewer does not start a receiver or change tuning. It reads JSONL
files from each enabled plugin's default `decoded/` directory, including older
generic-decoder logs for satellites that now have a dedicated decoder. Custom
standalone `--output` locations are not searched. Decoder consoles can still be
used separately to capture audio or replay recordings.

The reader filters by explicit satellite identity or callsign where available,
otherwise by the recorded input source. Partial JSONL writes are deferred until
the line is complete. It caches unchanged files and limits history to the latest
100 matching packets from the newest 50 logs per decoder, reading at most the
last 256 KiB of each. The dialog states those bounds; complete logs remain on disk.

Implementation: `telemetry_view.py`, `telemetry_view.js`, `telemetry_view.css`.
Main menu, dialog markup and read-only routes are in `bqe_wisp_web.py`.
Run `python -m unittest tests.test_telemetry_view -v` from the repository root.
Run `node tests/telemetry_charts.cjs` for the time-series checks.
Restart BQE and reload its web page to load this menu after upgrading.
