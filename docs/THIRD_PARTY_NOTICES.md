# Third-party sources and licenses

Project-original files are Apache-2.0 under the root LICENSE. This does not replace upstream licenses.

| Source | Use | License handling |
| --- | --- | --- |
| [porizou1 Qiita sample](https://qiita.com/porizou1/items/a085d20e936946330540), 2023-03-18 | Conceptual natural-language → model → ROS 2 → Gazebo TurtleBot3 reference. Original Python is reimplemented here with declarative validated plans. | No copied article code; no claim that the article is Apache-2.0. Attribution retained in README. |
| [Google Gen AI Python SDK](https://github.com/googleapis/python-genai), 2.29.0 | Official Vertex adapter; package dependency. | Apache-2.0 upstream package license remains with distribution. |
| [Gazebo gz-sim](https://github.com/gazebosim/gz-sim), a9c03d6b5ddea74023978200fe02166250d01f69 | SafeDiffDrive derives from Harmonic DiffDrive implementation. | Original Apache-2.0 headers retained; exact paths and changes in `src/pai_sim_safety/UPSTREAM.md`. Root LICENSE supplies the same license text. |
| [ROBOTIS turtlebot3](https://github.com/ROBOTIS-GIT/turtlebot3), [turtlebot3_simulations](https://github.com/ROBOTIS-GIT/turtlebot3_simulations) | Robot description/simulation dependencies fetched during build. | Retain each upstream LICENSE in checkout; source revisions recorded by environment build. |
| ROS 2 Jazzy, Nav2, Gazebo Harmonic | Runtime packages installed in container. | Package-specific upstream licenses remain in installed distributions. |

Do not remove upstream copyright headers or upstream LICENSE files when distributing a source checkout or container. This document records provenance; it does not relicense third-party code.
