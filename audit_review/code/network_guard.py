"""Allow local test fixtures, forbid hosted/network exchange connections."""
import ipaddress
import os
import socket


def install():
    os.environ['ARENA_TOKEN']='offline-audit'
    os.environ['RUN_LIVE_SMOKE']=''
    original=socket.socket.connect
    original_ex=socket.socket.connect_ex
    def allowed(address):
        if isinstance(address,str):return True  # local AF_UNIX
        host=address[0]
        if host=='localhost':return True
        try:return ipaddress.ip_address(host).is_loopback
        except ValueError:return False
    def connect(self,address):
        if not allowed(address):raise RuntimeError('Offline audit blocked a non-loopback connection')
        return original(self,address)
    def connect_ex(self,address):
        if not allowed(address):raise RuntimeError('Offline audit blocked a non-loopback connection')
        return original_ex(self,address)
    socket.socket.connect=connect
    socket.socket.connect_ex=connect_ex
