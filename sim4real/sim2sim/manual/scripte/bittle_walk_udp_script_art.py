import math

import carb
import numpy as np
from omni.isaac.kit import SimulationApp



# This sample loads an articulation and prints its information
import omni
from omni.isaac.dynamic_control import _dynamic_control
from omni.isaac.core.utils.nucleus import get_assets_root_path




JOINTS = [
    "left_front_shoulder_joint",
    "right_front_shoulder_joint",
    "right_back_shoulder_joint",
    "left_back_shoulder_joint",
    "left_front_knee_joint",
    "right_front_knee_joint",
    "right_back_knee_joint",
    "left_back_knee_joint",
]

# start simulation
omni.timeline.get_timeline_interface().play()

dc = _dynamic_control.acquire_dynamic_control_interface()
# Get handle to articulation
art = dc.get_articulation("/bittle")
if art == _dynamic_control.INVALID_HANDLE:
    print("*** '%s' is not an articulation" % "/bittle")
import asyncio
import socket
import time
async def my_task(host):
    print(f"my task begin")
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setblocking(0)
    s.bind(host)
    for i in range(3000):
        #print("sadsadsad:",i)
        try:
            data, addr = s.recvfrom(4096)
        except:
           await asyncio.sleep(0.01) #must ， gui not block
           continue
        print("recv:",data,addr)
        posvec = np.fromstring(data,np.float64)
        print('[Recieved] {} {}'.format(posvec, addr))
        try:
            #posvec = np.load("C:\\Users\\Administrator\\SynologyDrive\\sim4real\\sim2sim\\bittle_posvec.npy")
            #posvec = np.array([0,-75,0,-75,0,75,0,75])
            #posvec = posvec/180.0 * math.pi
            for joint, pos in zip(JOINTS, posvec):
                dof_ptr = dc.find_articulation_dof(art,joint)
                print("joint:",joint,pos,dof_ptr)
                #This should be called each frame of simulation if state on the articulation is being changed.
                dc.wake_up_articulation(art)
                #Set joint position target
                dc.set_dof_position_target(dof_ptr, pos)
        except Exception as e:
            print(str(e))
        await asyncio.sleep(0.01) #must , gui not block

addr = ('', 8080)
asyncio.ensure_future(my_task(addr))