#!/bin/python
# Copyright (c) 2026 Al Lawler, WB1BQE. All rights reserved.

#Downloads keps from amsat to a local file called keps.txt.   (Essentially just a wrapper for curl.)

import requests

# URL to fetch keps from
keps_url = 'https://www.amsat.org/tle/dailytle.txt' # Sat name\n line1\n line2\n

wizard_meteo_url='https://celestrak.org/NORAD/elements/gp.php?CATNR=57189&FORMAT=3le'
local_file = "keps.txt"

monitor3_url='https://celestrak.org/NORAD/elements/gp.php?CATNR=57180&FORMAT=3le'
local_file = 'keps.txt'

utmn2_url ='https://celestrak.org/NORAD/elements/gp.php?CATNR=57203&FORMAT=3le'
local_file = 'keps.txt'

def do_update(local_file, source_url, update_mode):  #update_mode is write for first call, then append for subsequent.

    try:
        # Send a GET request to the URL
        response = requests.get(source_url)
        response_text = response.text
        
        # Check if the request was successful (status code 200)
        if response.status_code == 200:
            # Open the local file in write mode
            with open(local_file, update_mode) as file:

                file.write(response_text)

            print(f"Content successfully downloaded from {source_url}")
        else:
            print(f"Failed to retrieve the content. HTTP Status code: {response.status_code}")
    except Exception as e:
        print(f"An error occurred: {e}")


# Function to remove blank lines from a text file (Keps for wizard-meteor contain odd line endings)
def remove_blank_lines(input_file, output_file):
    with open(input_file, 'r') as file:
        lines = file.readlines()

    # Filter out the blank lines
    non_blank_lines = [line for line in lines if line.strip() != '']

    # Write the non-blank lines to a new file
    with open(output_file, 'w') as file:
        file.writelines(non_blank_lines)


def main():
    do_update("keps.tmp", keps_url,"w")
    do_update("keps.tmp", wizard_meteo_url, "a") # rs38s wizard-meteo
    do_update("keps.tmp", monitor3_url, "a")     # Monitor-3
    do_update("keps.tmp", utmn2_url, "a")        # rs27/utmn-2

    remove_blank_lines("keps.tmp", "keps.txt")

if __name__ == "__main__":
    main()
