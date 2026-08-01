
bqe_wisp

  (Created with admiration and respect for Chris Jackson  (G7UPN) for his marvelous Wisp software many years ago)


This set of scripts mimics the major functions of the original wisp in a command line interface to track the many SSTV satellites that are currently available.   A browser based UI is also available once the server has been started.

BQE Wisp can download Keplerian elements,  then generate a schedule of satellites to be tracked,  then will wait for each satellite to come over the horizon and will tune to the downlink, and apply doppler tracking in real time.  

Other optional programs (For example, mmsstv, qsstv (Linux), telemetry decoders, or wsjtx) can be configured to be launched during specific satellite passes.   Likewise,  an "Idle task" including both a frequency to monitor, and a program to run while monitoring can be configured to maximize rig utilization and enjoyment between passes. 

Support:
  Best effort support is available from wb1bqe@gmail.com


To run:

  Prerequisites: Python3 and Hamlib visible within the users path

  Setup:
  
	Clone this repo
	update bqe_config/my_qth.yaml and bqe_config/my_rig.yaml as appropriate for your qth
	% python - Install python libraries using the following command(s)
 		python -m pip install numpy skyfield PyYaml argparse datetime



  To run:

	% python bqe_update_keps.py  (DO NOT RUN THIS MORE THAN ONCE PER DAY TO AVOID BEING BLOCKED BY DOWNLOAD SITE)
        % python bqe_schedule_passes.py  --nickname <sat_name1>  --nickname <sat_name2>  etc.

		Creates a file called schedule.json with pass information.   (Each satellite is referred to by a 			"nickname" which refers to a yaml file containing the specifics of the satellite.)


	% python bqe_wisp.py
		Reads the schedule file and waits for satellites to come into view.  As each satellite comes
		into view,  it calls bqe_track_continuously.py,  which performs doppler corrections during the pass.

   		Once BQE Wisp has been started,  the UI can be viewed at  http://localhost:8028


