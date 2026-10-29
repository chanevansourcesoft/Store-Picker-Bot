# store-picker-bot

An AI-powered dark store order picker: a mobile robot with an SO-100 arm that fulfils grocery orders autonomously, the way quick-commerce dark stores work.

Built for the Nebius x NVIDIA Global AI Hackathon, Physical AI track.

> Work in progress. The current focus is real-world data collection for GR00T on the robot.

## How it works

1. A customer types an order in natural language in the web app.
2. A Nemotron agent (served by Nebius Token Factory) interprets the order, checks inventory, substitutes out-of-stock items, and plans the pick order.
3. The robot drives to the shelf with Nav2 and a 2D LiDAR, and docks into a V-shaped guide confirmed by a limit switch.
4. The SO-100 arm picks each item from its bin and places it in the basket on the robot, driven by a GR00T N1.7 policy fine-tuned through LeRobot (one run per item; the instruction per product is in `policy/instructions.json`).
5. Pick verification (gripper feedback, load cell under the basket) is planned; there is no barcode scan.
6. The robot drives to the packing point and the web app shows the bill.

## NVIDIA and Nebius

| Component | Used for |
|---|---|
| NVIDIA GR00T N1.7 (via LeRobot) | Vision-language-action policy for the arm, running on the Jetson |
| NVIDIA Cosmos | Augmenting real demonstrations with lighting and background variations |
| NVIDIA Nemotron | Order understanding and planning agent |
| NVIDIA Jetson AGX Orin 64GB | All on-robot compute |
| Nebius AI Cloud | GR00T fine-tuning |
| Nebius Token Factory | Serving Nemotron through an OpenAI-compatible API |

## Pipeline

1. Record real teleoperated demonstrations (SO-100 leader → follower) on the Jetson with LeRobot.
2. Augment them with Cosmos.
3. Fine-tune GR00T on Nebius AI Cloud.
4. Run the fine-tuned policy on the same Jetson.

No simulation is used; all data comes from the real robot.

## Hardware

- Jetson AGX Orin 64GB (JetPack 6.2, Ubuntu 22.04, ROS 2 Humble)
- SO-100 follower and leader arms (Feetech STS3215 servos)
- Wrist camera and scene camera
- Differential-drive base with wheel encoders, 2D LiDAR
- Load cell (HX711) under the tray

## Repository layout

```
policy/      LeRobot project: GR00T data collection and pick service (owns arm and cameras)
ros2_ws/     ROS 2 workspace: bringup, base driver, docking, tray scale, task executor
agent/       Shared Nemotron agent (store_agent) and the robot API server (robot_server, runs on the Jetson)
storepilot/  The apps: StorePilot AI touchscreen dashboard (backend/, frontend/) and the
             with the shared Nemotron agent in agent/
docs/        Architecture notes and tool feedback
```

## Setup

Copy `.env.example` to `.env` and fill in your credentials. Detailed setup instructions will be added as each component lands.

## License

Apache 2.0. See [LICENSE](LICENSE).
