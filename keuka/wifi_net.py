# wifi_net.py
# Backward compatibility wrapper for networking.wifi module

from .networking.wifi import *

# This file maintains the original wifi_net.py interface
# while delegating to the new networking.wifi module.