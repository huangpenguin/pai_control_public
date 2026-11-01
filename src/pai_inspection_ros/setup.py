from setuptools import find_packages, setup
from glob import glob
setup(name="pai_inspection_ros", version="0.1.0", packages=find_packages(),
 data_files=[("share/ament_index/resource_index/packages", ["resource/pai_inspection_ros"]),
 ("share/pai_inspection_ros", ["package.xml"]),
 ("share/pai_inspection_ros/launch", glob("launch/*.py")),
 ("share/pai_inspection_ros/config", glob("config/*"))],
 install_requires=["setuptools"], zip_safe=True,
 entry_points={"console_scripts": ["safety_guard = pai_inspection_ros.safety_guard:main", "instruction_executor = pai_inspection_ros.instruction_executor:main", "instruction_cli = pai_inspection_ros.instruction_cli:main"]})
