#!/usr/bin/env python3

# Copyright (c) 2023, Tinker Twins
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:

# 1. Redistributions of source code must retain the above copyright notice, this
#    list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

# ROS2 module imports
import rclpy # ROS2 client library (rcl) for Python (built on rcl C API)
from rclpy.node import Node # Node class for Python nodes
from geometry_msgs.msg import Twist # Twist (linear and angular velocities) message class
from tf2_msgs.msg import TFMessage # Transforms message class
from rclpy.qos import QoSProfile, QoSReliabilityPolicy, QoSHistoryPolicy # Ouality of Service (tune communication between nodes)
from rclpy.duration import Duration # Time duration class
from tf2_ros.transform_listener import TransformListener # Transform (tf2) listener
from tf2_ros.buffer import Buffer # Transform buffer
from tf2_ros import TransformException # Transform exception

# Python mudule imports
import queue # FIFO queue
import time # Tracking time

# PID controller class
class PIDController:
    '''
    Generates control action taking into account instantaneous error (proportional action),
    accumulated error (integral action) and rate of change of error (derivative action).
    '''
    def __init__(self, kP, kI, kD, kS):
        self.kP       = kP # Proportional gain
        self.kI       = kI # Integral gain
        self.kD       = kD # Derivative gain
        self.kS       = kS # Saturation constant (error history buffer size)
        self.err_int  = 0 # Error integral
        self.err_dif  = 0 # Error difference
        self.err_prev = 0 # Previous error
        self.err_hist = queue.Queue(self.kS) # Limited buffer of error history
        self.t_prev   = 0 # Previous time

    def control(self, err, t):
        '''
        Generate PID controller output.
        :param err: Instantaneous error in control variable w.r.t. setpoint
        :param t  : Current timestamp
        :return u: PID controller output
        '''
        dt = t - self.t_prev # Timestep
        if dt > 0.0:
            self.err_hist.put(err) # Update error history
            self.err_int += err # Integrate error
            if self.err_hist.full(): # Jacketing logic to prevent integral windup
                self.err_int -= self.err_hist.get() # Rolling FIFO buffer
            self.err_dif = (err - self.err_prev) # Error difference
            u = (self.kP * err) + (self.kI * self.err_int * dt) + (self.kD * self.err_dif / dt) # PID control law
            self.err_prev = err # Update previos error term
            self.t_prev = t # Update timestamp
            return u # Control signal

# Node class
class RobotController(Node):

    #######################
    '''Class constructor'''
    #######################

    def __init__(self):
        # Information and debugging
        info = '\nMake the robot detect and track AprilTag markers.\n'
        print(info)
        # ROS2 infrastructure
        super().__init__('robot_controller') # Create a node with name 'robot_controller'
        qos_profile = QoSProfile( # Ouality of Service profile
        reliability=QoSReliabilityPolicy.RMW_QOS_POLICY_RELIABILITY_RELIABLE, # Reliable (not best effort) communication
        history=QoSHistoryPolicy.RMW_QOS_POLICY_HISTORY_KEEP_LAST, # Keep/store only up to last N samples
        depth=10 # Queue size/depth of 10 (only honored if the “history” policy was set to “keep last”)
        )
        self.robot_ctrl_pub = self.create_publisher(Twist, '/cmd_vel', qos_profile) # Publisher which will publish Twist message to the topic '/cmd_vel' adhering to 'qos_profile' QoS profile
        timer_period = 0.001 # Node execution time period (seconds)
        self.timer = self.create_timer(timer_period, self.robot_controller_callback) # Define timer to execute 'robot_controller_callback()' every 'timer_period' seconds
        self.ctrl_msg = Twist() # Robot control commands (twist)
        self.start_time = self.get_clock().now() # Record current time in seconds
        self.pid_lon = PIDController(0.06, 0.001, 0.05, 10) # Longitudinal PID controller object initialized with kP, kI, kD, kS
        self.pid_lat = PIDController(2.5, 0.01, 0.2, 10) # Lateral PID controller object initialized with kP, kI, kD, kS
        self.tf_buffer = Buffer() # Transform buffer
        self.tf_listener = TransformListener(self.tf_buffer, self) # Transform listener

    #######################
    '''Callback function'''
    #######################

    def robot_controller_callback(self):
        DELAY = 4.0 # Time delay (s)
        if self.get_clock().now() - self.start_time > Duration(seconds=DELAY):
            to_frame_rel = 'camera'
            from_frame_rel = 'tag36h11_0'
            try:
                tf2_msg = self.tf_buffer.lookup_transform(to_frame_rel, from_frame_rel, rclpy.time.Time())
            except TransformException as e:
                # self.get_logger().info(f'Could not transform {to_frame_rel} to {from_frame_rel}: {e}')
                print('No AprilTag marker found, looking for one...')
                return
            lon_error = tf2_msg.transform.translation.z # Calculate longitudinal error w.r.t. AprilTag marker
            lat_error = -tf2_msg.transform.translation.x # Calculate lateral error w.r.t. AprilTag marker
            tstamp = time.time() # Current timestamp (s)
            LIN_VEL = self.pid_lon.control(lon_error, tstamp) # Linear velocity (m/s)
            ANG_VEL = self.pid_lat.control(lat_error, tstamp) # Angular velocity (rad/s)
            self.ctrl_msg.linear.x = LIN_VEL # Set linear velocity
            self.ctrl_msg.angular.z = ANG_VEL # Set angular velocity
            self.robot_ctrl_pub.publish(self.ctrl_msg) # Publish robot controls message
            print('Deviation from AprilTag marker {}, {}'.format(round(lon_error, 4), round(lat_error, 4)))
            #print('Robot moving with {} m/s and {} rad/s'.format(LIN_VEL, ANG_VEL))
        else:
            print('Initializing...')

def main(args=None):
    rclpy.init(args=args) # Start ROS2 communications
    node = RobotController() # Create node
    rclpy.spin(node) # Execute node
    node.destroy_node() # Destroy node explicitly (optional - otherwise it will be done automatically when garbage collector destroys the node object)
    rclpy.shutdown() # Shutdown ROS2 communications

if __name__ == "__main__":
    main()
