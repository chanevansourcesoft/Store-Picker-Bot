# syntax=docker/dockerfile:1
# ROS 2 Humble for the mecanum base: board driver, lidar, SLAM and Nav2.
# Runs on the Jetson AGX Orin (arm64) and also builds on an x86 PC. No GPU needed.
# The workspace itself (src/) is not copied in: compose.yaml mounts it at /home/ubuntu/ros2_ws,
# the path the launch files and configs expect, and `colcon build` runs inside the container.
FROM ros:humble-ros-base-jammy@sha256:566b87e6dfc48392189a9c32e594e84060fa9948de93b04e536f39475a1f6963

ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential git libeigen3-dev python3-colcon-common-extensions python3-pip \
        python3-numpy python3-serial python3-yaml \
        ros-humble-navigation2 ros-humble-nav2-common \
        ros-humble-slam-toolbox ros-humble-robot-localization ros-humble-laser-filters \
        ros-humble-imu-tools ros-humble-robot-state-publisher ros-humble-joint-state-publisher \
        ros-humble-xacro ros-humble-rviz2 ros-humble-nav2-rviz-plugins \
    && rm -rf /var/lib/apt/lists/*
RUN pip3 install --no-cache-dir transforms3d==0.4.1

# Drivers the launch files use that are not on apt: the A1 lidar driver and laser odometry.
# Pinned commits, built once into /opt/ros_deps.
WORKDIR /opt/ros_deps/src
RUN git clone https://github.com/Slamtec/sllidar_ros2.git \
    && git -C sllidar_ros2 checkout 34300099fadfc772965962dec837bf436706188f \
    && git clone -b ros2 https://github.com/MAPIRlab/rf2o_laser_odometry.git \
    && git -C rf2o_laser_odometry checkout b38c68e46387b98845ecbfeb6660292f967a00d3
WORKDIR /opt/ros_deps
RUN . /opt/ros/humble/setup.sh \
    && colcon build --merge-install --cmake-args -DCMAKE_BUILD_TYPE=Release \
    && rm -rf build log

# The container runs as the host user, so let any user write the mount point's parent.
RUN mkdir -p /home/ubuntu/ros2_ws && chmod 777 /home/ubuntu
COPY docker-entrypoint.sh /docker-entrypoint.sh
WORKDIR /home/ubuntu/ros2_ws
ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["bash"]
