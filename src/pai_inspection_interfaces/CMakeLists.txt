cmake_minimum_required(VERSION 3.16)
project(pai_inspection_interfaces)
find_package(ament_cmake REQUIRED)
find_package(rosidl_default_generators REQUIRED)
rosidl_generate_interfaces(${PROJECT_NAME}
  "action/ExecuteInstruction.action" "msg/SafetyState.msg" "srv/SetMotionPermit.srv")
ament_export_dependencies(rosidl_default_runtime)
ament_package()
