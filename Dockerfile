FROM ros:jazzy-ros-base-noble@sha256:066420e07f60aa18262f2479981def87ebcfcec42eefb0c0c57c4a46098348ca AS simulation_base
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential git ca-certificates python3-vcstool python3-colcon-common-extensions \
    python3-venv python3-yaml mesa-utils libgl1-mesa-dri libegl1 \
    ros-jazzy-ros-gz ros-jazzy-navigation2 ros-jazzy-nav2-bringup \
    ros-jazzy-gz-sim-vendor ros-jazzy-gz-plugin-vendor ros-jazzy-gz-transport-vendor \
    ros-jazzy-gz-msgs-vendor ros-jazzy-tf2-tools ros-jazzy-std-srvs \
    ros-jazzy-action-msgs ros-jazzy-nav2-msgs ros-jazzy-rosgraph-msgs \
    ros-jazzy-rclpy ros-jazzy-tf2-ros \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /workspace
COPY dependencies.repos /opt/pai/dependencies.repos
RUN mkdir -p src && vcs import src < /opt/pai/dependencies.repos
RUN source /opt/ros/jazzy/setup.bash && \
    colcon build --parallel-workers 4 --packages-select turtlebot3_description turtlebot3_msgs turtlebot3_gazebo \
      --cmake-args -DCMAKE_BUILD_TYPE=Release

FROM simulation_base AS runtime
COPY src/ /workspace/src/
RUN source /opt/ros/jazzy/setup.bash && source /workspace/install/setup.bash && \
    colcon build --base-paths /workspace/src/pai_sim_safety /workspace/src/pai_inspection_interfaces /workspace/src/pai_inspection_ros --parallel-workers 4 \
      --cmake-args -DCMAKE_BUILD_TYPE=Release
COPY core/ /opt/pai/core/
COPY pyproject.toml requirements.lock /opt/pai/
RUN python3 -m venv --system-site-packages /opt/pai/venv && \
    /opt/pai/venv/bin/pip install --no-cache-dir -r /opt/pai/requirements.lock && \
    /opt/pai/venv/bin/pip install --no-deps --no-build-isolation -e /opt/pai
COPY simulation/ /opt/pai/simulation/
COPY scripts/ /opt/pai/scripts/
COPY docs/ /opt/pai/docs/
COPY README.md LICENSE /opt/pai/
RUN source /opt/ros/jazzy/setup.bash && source /workspace/install/setup.bash && \
    python3 /opt/pai/scripts/prepare_simulation.py && \
    dpkg-query -W > /opt/pai/debian-packages.lock
ENV TURTLEBOT3_MODEL=waffle_pi ROS_DOMAIN_ID=87 ROS_LOCALHOST_ONLY=1 \
    GZ_PARTITION=pai_inspection_p1 LIBGL_ALWAYS_SOFTWARE=1 GALLIUM_DRIVER=llvmpipe \
    EGL_PLATFORM=surfaceless LP_NUM_THREADS=4 PYTHONDONTWRITEBYTECODE=1 \
    HOME=/tmp/pai-home PYTHONUNBUFFERED=1
ENTRYPOINT ["/opt/pai/scripts/container_entrypoint.sh"]
CMD ["ros2", "launch", "/opt/pai/simulation/launch/bringup.launch.py"]
