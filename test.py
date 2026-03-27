import airsim
import time

print("Script started ✅")

client = airsim.MultirotorClient()
client.confirmConnection()

print("Connected to AirSim ✅")

client.enableApiControl(True)
print("API Control:", client.isApiControlEnabled())

client.armDisarm(True)
print("Armed!")

print("Taking off...")
client.takeoffAsync().join()

time.sleep(2)

print("Landing...")
client.landAsync().join()

client.armDisarm(False)
client.enableApiControl(False)

print("Done ✅")