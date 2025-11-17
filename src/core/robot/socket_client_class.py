"""
Robot Socket Client
TCP/IP communication with robot controller.
"""

import socket
import time
import yaml
import os

class RobotSocketClient:
    """
    Socket client for robot communication.
    Maintains connection state and handles message protocol.
    """
    
    def __init__(self):
        self.server_socket = None
        self.client_socket = None
        self.client_ip = None
    
    def load_sequence(self, path="actions.yaml"):
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        steps = data["RobotSequence"]["steps"]
        # ensure we iterate in id order
        steps = sorted(steps, key=lambda s: s.get("id", 0))
        return steps
    
    def sendToRobot(self, sendData):
        server_message = sendData
        self.client_socket.send(server_message.encode("UTF-8"))
        print("send Objekt !")
        if self.client_socket.recv:
            print("the message is:")
            client_message = self.client_socket.recv(4094)
            client_message = client_message.decode("latin-1")
            print("!!", client_message)
        return "client_message"
    
    def connect_robot(self, host=None, port=None):
        if host is None:
            host = os.getenv('ROBOT_SOCKET_HOST', '127.0.0.1')
        if port is None:
            port = int(os.getenv('ROBOT_SOCKET_PORT', 5000))
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.bind((host, port))
        self.server_socket.listen()
        print(f"Waiting for robot connection on {host}:{port}...")
        (self.client_socket, self.client_ip) = self.server_socket.accept()
        print(f"Robot at address {self.client_ip} connected.")
        return True
    
    def execute_sequence(self, yaml_path="actions.yaml"):
        steps = self.load_sequence(yaml_path)
        for step in steps:
            action = step.get("action", "").strip()
            target = step.get("target", "").strip()
            stabilize = step.get("stabilize", "")
            tool = step.get("tool", "").strip()
            position = step.get("position", "").strip()
            client_message = self.sendToRobot(f'act:{action},tar:{target},sta:{stabilize},tool:{tool},pos:{position}',)
            if client_message == "Shutting down":
                return False
        return True
    
    
    def is_connected(self):
        return self.client_socket is not None