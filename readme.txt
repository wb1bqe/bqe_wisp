
bqe_wisp

  (Created with admiration and respect for Chris Jackson  (G7UPN) for his marvelous Wisp software many years ago)


This set of scripts mimics the major functions of the original wisp in a command line interface to track the many SSTV satellites that are currently available.   A browser based UI is also available once the server has been started.

BQE Wisp can download Keplerian elements,  then generate a schedule of satellites to be tracked,  then will wait for each satellite to come over the horizon and will tune to the downlink, and apply doppler tracking in real time.  

Other optional programs (For example, mmsstv, Yoniq, qsstv (Linux), telemetry decoders, or wsjtx) can be configured to be launched during specific satellite passes.   Likewise,  an "Idle task" including both a frequency to monitor, and a program to run while monitoring can be configured to maximize rig utilization and enjoyment between passes. 

Support:
  Best effort support is available from wb1bqe@gmail.com


To run:

  Prerequisites: Python3 and Hamlib visible within the users path

  Setup:
  
	Clone this repo
	update bqe_config/my_qth.yaml and bqe_config/my_rig.yaml as appropriate for your qth
	% python - Install python libraries using the following command(s)
 		python -m pip install numpy skyfield PyYaml argparse datetime soundcard lameenc



  To run:

	% python bqe_update_keps.py  (DO NOT RUN THIS MORE THAN ONCE PER DAY TO AVOID BEING BLOCKED BY DOWNLOAD SITE)
        % python bqe_schedule_passes.py  --nickname <sat_name1>  --nickname <sat_name2>  etc.

		Creates a file called schedule.json with pass information.   (Each satellite is referred to by a 			"nickname" which refers to a yaml file containing the specifics of the satellite.)


	% python bqe_wisp.py
		Reads the schedule file and waits for satellites to come into view.  As each satellite comes
		into view,  it calls bqe_track_continuously.py,  which performs doppler corrections during the pass.

   		Once BQE Wisp has been started,  the UI can be viewed at  http://localhost:8028



Settings backup and restore
---------------------------
Use Admin > Backup settings from the localhost web console. Browse to a
destination folder on the BQE WISP computer and save a timestamped ZIP.
The backup includes every file/subfolder in bqe_config and, when present,
plugins/bqe_aprs_decoder/aprs-is.private.json (your APRS-IS credential/settings).
The ZIP is not encrypted.
Use Admin > Restore settings to browse to a backup ZIP and confirm restoration.
Matching files are overwritten; files absent from the backup are kept. Older
config-only backups leave the existing APRS-IS credential unchanged. Restart
BQE WISP after restoring to apply all settings. ZIPs are validated before any
settings are changed; supported restores are up to 512 MB and 10,000 entries.

Downloaded data backup and restore
----------------------------------
Admin > Backup Downloaded Data saves a timestamped ZIP to a browsed folder.
It includes sstv_gallery, a separately configured SSTV gallery when applicable,
the top-level decoded folder (if present), and plugins/*/decoded, including
APRS packet logs/map history and all nested files, sidecars and empty folders.
Also included: plugins/bqe_sound_recorder/recordings, its separately configured
recordings folder, plugins/bqe_sdr/recordings (WAV/IQ files and metadata), and
plugins/bqe_aprs_decoder/upload_receipts. Speech transcript JSON/text sidecars
are included alongside the audio. Model downloads, generated voice caches and
temporary .uploads folders are not included. Custom standalone SDR --output
folders outside plugins/bqe_sdr/recordings require a separate backup.
LRPT weather data is excluded, including everything in bqe_lrpt_decoder.
APRS credentials remain in the separate settings backup.
Admin > Restore downloaded data validates the ZIP and overwrites matching
files, preserving other files. Custom gallery and pass-recording data use the
current configured locations. Stop reception, recording, decoding and
transcription before backup or restore; refresh galleries, recordings and
APRS/telemetry views afterward. Limits: 64 GB / 250,000 entries.
Settings and downloaded-data backups must be restored using their respective menus.
