
def select_antenna(desired_az, desired_el):

# Determine what antenna we want to select based on az & el (duplicate of RCS-10 rules )

# Here are all the user changeable rules 
  
# Current Antenna Assignments  
# 1 SE Yagi
# 2 empty
# 3 Soutwest yagi 
# 4 Vertical in Tree
# 5 empty
# 6 empty
# 7 NE Yagi
# 8 NW Yagi
  
# Modified for UHF antennas.  2m is NOT on switch.  
  if 0 <= float(desired_az) < 90:
      print ('Selecting Antenna 7 (NE Yagi)\n')  
      return 7

  elif 90 <= float(desired_az) < 220:
      print ('Selecting Antenna 1 (Dualband UFH)\n')  #########UHF 
      return 1

  elif 220 <= float(desired_az) < 260:
      print('Selecting antenna 2 UHF on pole \n')  #########UHF
      return 2

  elif 260 <= float(desired_az) < 360 :
      print ('Selecting Antenna 8 NW Yagi)\n')#########UHF
      return 8
  else:
      print('Default choice Selecting Antenna 2 (UHF on pole)\n')  #########UHF
      return 2

def do_tracking(desired_antenna):
    print("Selecting antenna", desired_antenna)
    curl_payload = "Antenna+Choice="+ str(desired_antenna)

    # Define the antenna switch URL and message to post
    url = "http://192.168.1.110:8000"
    data = {"message": curl_payload}

    # Send the POST request
    response = requests.post(url, data=data) 

    # Check the response status
    if response.status_code == 200:
        print("Message posted successfully!")
    else:
        print(f"Failed to post message. Status code: {response.status_code}")




def main():
    print("hi")

main()
