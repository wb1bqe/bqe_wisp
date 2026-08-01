
bqe_wisp

  (Created with admiration and respect to Chris Jackson  (G7UPN) for his marvelous wisp software many years ago)


This set of scripts mimics the major functions of wisp in a command line interface to track the many SSTV microsats currently available. 

It can download keps,  then generate a schedule of satellites to be tracked,  then will wait for each satellite to come over the horizon and will tune to the downlink, and apply doppler tracking in real time.   Support for FM repeater satellites will be added shortly.

This software does not currently run any other software at the beginning of the pass.  (The expected use case is that mmsstv or similar will already be running and waiting for audio from the satellites as they come overhead.) 

Satellites have priorities.  In the event of conflicting passes, the higher priority satellite will be selected. (This still needs to be tested, and more intelligent decision making about sharing a timeslot between satellites might be possible.)

In between passes, the rig can be switched to 14.230 (Or other user selectable frequency/mode).  



To run:

  Prerequisites: Python3 and Hamlib visible within the users path

  Setup:
  
	Clone this repo
	update my_qth.yaml and my_rig.yaml as appropriate for your qth
	% python - Install python libraries using the following command(s)
 		python -m pip install numpy skyfield PyYaml argparse datetime



  To run:

	% python bqe_update_keps.py
        % python bqe_schedule_passes.py  --nickname <sat_name1>  --nickname <sat_name2>  etc.

		Creates a file called schedule.json with pass information.   (Each satellite is referred to by a 			"nickname" which refers to a yaml file containing the specifics of the satellite.)


	% python bqe_wisp.py
		Reads the schedule file and waits for satellites to come into view.  As each satellite comes
		into view,  it calls bqe_track_continuously.py,  which performs doppler corrections during the pass.
